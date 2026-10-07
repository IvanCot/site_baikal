from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from hotel.forms import BookingForm
from hotel.models import AuditLog, Booking, Guest, Payment, Room, User
from hotel.services import transition


@override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=['testserver', 'localhost'])
class WorkflowTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('admin', password='Test-pass-4638!', role='owner')
        self.employee = User.objects.create_user('employee', password='Test-pass-4638!')
        self.room = Room.objects.create(name='№11', beds=2, capacity=3, daily_rate=Decimal('7500'))
        self.guest = Guest.objects.create(full_name='Иванов Иван', phone='+7 900 100-00-00')
        self.now = timezone.now().replace(second=0, microsecond=0)
        self.booking = self.make_booking()

    def make_booking(self, **kwargs):
        values = {'room': self.room, 'primary_guest': self.guest, 'check_in': self.now - timedelta(hours=1),
                  'check_out': self.now + timedelta(days=2), 'guest_count': 2, 'total_cost': Decimal('15000'), 'linen_sets': 2}
        values.update(kwargs)
        return Booking.objects.create(**values)

    def payment(self, amount):
        return Payment.objects.create(booking=self.booking, amount=Decimal(amount), method='transfer', created_by=self.admin)

    def booking_data(self, **kwargs):
        values = {'room': self.room.pk, 'primary_guest': self.guest.pk,
                  'check_in': timezone.localtime(self.booking.check_out).strftime('%Y-%m-%dT%H:%M'),
                  'check_out': timezone.localtime(self.booking.check_out + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M'),
                  'guest_count': 2, 'total_cost': '15000.00', 'linen_sets': 2, 'prepayment': '5000', 'payment_method': 'transfer'}
        values.update(kwargs)
        return values

    def test_overlap_all_shapes(self):
        start, end = self.booking.check_in, self.booking.check_out
        for check_in, check_out in [(start, end), (start - timedelta(hours=1), end + timedelta(hours=1)),
                                    (start + timedelta(hours=1), end - timedelta(hours=1)),
                                    (start - timedelta(hours=1), start + timedelta(minutes=1)),
                                    (end - timedelta(minutes=1), end + timedelta(hours=1))]:
            with self.subTest(check_in=check_in, check_out=check_out), self.assertRaises(ValidationError):
                self.make_booking(check_in=check_in, check_out=check_out)

    def test_adjacent_intervals_allowed(self):
        self.make_booking(check_in=self.booking.check_out, check_out=self.booking.check_out + timedelta(days=1))
        self.make_booking(check_out=self.booking.check_in, check_in=self.booking.check_in - timedelta(days=1))
        self.assertEqual(Booking.objects.count(), 3)

    def test_cancelled_and_completed_do_not_block(self):
        self.make_booking(status='cancelled')
        self.make_booking(status='completed')
        self.assertEqual(Booking.objects.count(), 3)

    def test_edit_conflict(self):
        other = self.make_booking(check_in=self.booking.check_out, check_out=self.booking.check_out + timedelta(days=1))
        other.check_in -= timedelta(minutes=1)
        with self.assertRaises(ValidationError):
            other.save()

    def test_paid_balance_and_statuses(self):
        self.assertEqual(self.booking.paid, Decimal('0'))
        self.assertEqual(self.booking.balance, Decimal('15000'))
        self.assertEqual(self.booking.financial_code, 'unpaid')
        self.payment('5000')
        self.assertEqual(self.booking.paid, Decimal('5000'))
        self.assertEqual(self.booking.balance, Decimal('10000'))
        self.assertEqual(self.booking.financial_code, 'partial')
        self.payment('10000')
        self.assertEqual(self.booking.balance, Decimal('0'))
        self.assertEqual(self.booking.financial_code, 'paid')
        self.payment('1.23')
        self.assertEqual(self.booking.balance, Decimal('-1.23'))
        self.assertEqual(self.booking.financial_code, 'overpaid')

    def test_invalid_values(self):
        for params in [{'guest_count': 0}, {'guest_count': 4}, {'total_cost': Decimal('-1')}, {'check_out': self.booking.check_in}]:
            with self.subTest(params=params), self.assertRaises(ValidationError):
                self.make_booking(status='cancelled', **params)
        with self.assertRaises(ValidationError):
            self.payment('0')

    def test_checkin_checkout_cleaning(self):
        transition(self.booking.pk, 'checkin', self.employee)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, 'occupied')
        self.assertIsNotNone(self.booking.actual_check_in)
        self.assertEqual(self.room.state[0], 'occupied')
        transition(self.booking.pk, 'checkout', self.employee)
        self.booking.refresh_from_db()
        self.room.refresh_from_db()
        self.assertEqual(self.booking.status, 'completed')
        self.assertIsNotNone(self.booking.actual_check_out)
        self.assertEqual(self.room.service_status, 'cleaning')
        self.client.force_login(self.employee)
        self.client.post(reverse('room_clean', args=[self.room.pk]))
        self.room.refresh_from_db()
        self.assertEqual(self.room.service_status, 'ready')
        self.assertEqual(self.room.state[0], 'free')
        self.assertTrue(AuditLog.objects.filter(action='Выселение').exists())

    def test_invalid_transition_and_early_checkin(self):
        with self.assertRaises(ValidationError):
            transition(self.booking.pk, 'checkout', self.employee)
        self.booking.check_in = self.now + timedelta(hours=1)
        self.booking.save()
        with self.assertRaises(ValidationError):
            transition(self.booking.pk, 'checkin', self.employee)

    def test_actual_stay_blocks_second_checkin(self):
        self.booking.status = 'occupied'
        self.booking.check_out = self.now - timedelta(minutes=10)
        self.booking.save()
        other = self.make_booking(check_in=self.now - timedelta(minutes=5), check_out=self.now + timedelta(days=1))
        with self.assertRaises(ValidationError):
            transition(other.pk, 'checkin', self.employee)

    def test_cleaning_blocks_checkin_but_allows_future_reservation(self):
        self.room.service_status = 'cleaning'
        self.room.save()
        with self.assertRaises(ValidationError):
            transition(self.booking.pk, 'checkin', self.employee)
        self.make_booking(check_in=self.booking.check_out, check_out=self.booking.check_out + timedelta(days=1))

    def test_admin_can_cancel(self):
        transition(self.booking.pk, 'cancel', self.employee)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, 'cancelled')

    def test_no_booking_deletion(self):
        with self.assertRaises(ValidationError):
            self.booking.delete()
        with self.assertRaises(ValidationError):
            Booking.objects.filter(pk=self.booking.pk).delete()

    def test_admin_permissions(self):
        self.client.force_login(self.employee)
        for name in ['dashboard', 'calendar', 'bookings', 'rooms', 'history', 'booking_create', 'guests', 'guest_create', 'payments', 'payment_create', 'settings', 'guest_search', 'statistics']:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        for name in ['audit', 'user_create', 'room_create']:
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)
            self.assertEqual(self.client.post(reverse(name), {'role': 'owner'}).status_code, 403)
        detail = self.client.get(reverse('booking_detail', args=[self.booking.pk]))
        self.assertContains(detail, 'Стоимость')
        self.assertNotContains(detail, 'Паспорт гостя')

    def test_admin_pages_render(self):
        self.client.force_login(self.admin)
        for name in ['dashboard', 'calendar', 'bookings', 'history', 'rooms', 'guests', 'payments', 'settings', 'audit',
                     'booking_create', 'room_create', 'guest_create', 'payment_create', 'user_create', 'password_change']:
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'lang="ru"')
        for name, pk in [('booking_detail', self.booking.pk), ('booking_edit', self.booking.pk), ('guest_detail', self.guest.pk),
                         ('guest_edit', self.guest.pk), ('room_edit', self.room.pk), ('user_edit', self.employee.pk)]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name, args=[pk])).status_code, 200)

    def test_booking_create_with_prepayment(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('booking_create'), self.booking_data())
        self.assertEqual(response.status_code, 302)
        created = Booking.objects.exclude(pk=self.booking.pk).get()
        self.assertEqual(created.additional_guests.count(), 0)
        self.assertEqual(created.paid, Decimal('5000'))
        self.assertEqual(created.total_cost, Decimal('7500'))
        self.assertTrue(AuditLog.objects.filter(action='Создание бронирования').exists())

    def test_new_guest_and_inline_validation(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('booking_create'), self.booking_data(primary_guest='', new_guest_name='Новый Гость', new_guest_phone='+7 111'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Guest.objects.filter(full_name='Новый Гость').exists())
        form = BookingForm(data=self.booking_data(primary_guest='', new_guest_name='', new_guest_phone=''))
        self.assertFalse(form.is_valid())

    def test_backend_conflict_does_not_create_guest_or_payment(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('booking_create'), self.booking_data(primary_guest='', new_guest_name='Не сохранить', new_guest_phone='123',
            check_in=timezone.localtime(self.booking.check_in).strftime('%Y-%m-%dT%H:%M')))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'уже забронирован')
        self.assertFalse(Guest.objects.filter(full_name='Не сохранить').exists())
        self.assertEqual(Payment.objects.count(), 0)

    def test_finance_filters(self):
        self.payment('5000')
        self.client.force_login(self.admin)
        response = self.client.get(reverse('bookings'), {'finance': 'partial', 'q': self.guest.full_name})
        self.assertContains(response, self.booking.number)
        self.assertNotContains(self.client.get(reverse('bookings'), {'finance': 'paid'}), self.booking.number)

    def test_finance_filter_with_multiple_companions(self):
        from hotel.models import BookingGuest
        for i in range(2):
            g = Guest.objects.create(full_name=f'Гость {i}', phone=f'123{i}')
            BookingGuest.objects.create(booking=self.booking, guest=g)
        self.payment('10000')
        self.client.force_login(self.admin)
        response = self.client.get(reverse('bookings'), {'finance': 'partial', 'q': self.guest.full_name})
        self.assertContains(response, self.booking.number)

    def test_payment_edit_delete_changes_balance(self):
        self.client.force_login(self.admin)
        payment = self.payment('5000')
        data = {'booking': self.booking.pk, 'paid_at': timezone.localtime(self.now).strftime('%Y-%m-%dT%H:%M'), 'amount': '10000', 'method': 'cash'}
        self.assertEqual(self.client.post(reverse('payment_edit', args=[payment.pk]), data).status_code, 302)
        self.assertEqual(self.booking.balance, Decimal('5000'))
        self.assertEqual(self.client.post(reverse('payment_delete', args=[payment.pk])).status_code, 302)
        self.assertEqual(self.booking.balance, Decimal('15000'))
        self.assertTrue(AuditLog.objects.filter(action='Удаление платежа').exists())

    def test_csrf_and_post_only(self):
        self.client.force_login(self.employee)
        self.assertEqual(self.client.get(reverse('booking_action', args=[self.booking.pk, 'checkin'])).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.employee)
        self.assertEqual(csrf_client.post(reverse('booking_action', args=[self.booking.pk, 'checkin'])).status_code, 403)

    def test_last_admin_and_self_disable_prohibited(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('user_edit', args=[self.admin.pk]), {'role': 'admin', 'is_active': 'on'})
        self.assertContains(response, 'Нельзя отключить собственный доступ')
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, 'owner')

    def test_protected_history_relations(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('room_delete', args=[self.room.pk]))
        self.assertTrue(Room.objects.filter(pk=self.room.pk).exists())
        self.client.post(reverse('guest_delete', args=[self.guest.pk]))
        self.assertTrue(Guest.objects.filter(pk=self.guest.pk).exists())

    def test_room_cannot_be_disabled_with_active_bookings(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('room_edit', args=[self.room.pk]),
            {'name': self.room.name, 'beds': 2, 'capacity': 3, 'daily_rate': '7500', 'service_status': 'unavailable'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'действующие бронирования')
        self.room.refresh_from_db()
        self.assertTrue(self.room.active)
        self.assertEqual(self.room.service_status, 'ready')

    @skipUnless(connection.vendor == 'postgresql', 'Требуется PostgreSQL')
    def test_postgres_constraint_protects_bulk_insert(self):
        conflict = Booking(number='BULKTEST01', room=self.room, primary_guest=self.guest, check_in=self.booking.check_in,
                           check_out=self.booking.check_out, guest_count=1, total_cost=Decimal('100'))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Booking.objects.bulk_create([conflict])

    @skipUnless(connection.vendor == 'postgresql', 'Требуется PostgreSQL')
    def test_postgres_trigger_prevents_raw_delete(self):
        with self.assertRaises(Exception), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('DELETE FROM hotel_booking WHERE id = %s', [self.booking.pk])
        self.assertTrue(Booking.objects.filter(pk=self.booking.pk).exists())
