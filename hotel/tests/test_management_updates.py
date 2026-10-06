from datetime import date, datetime, timedelta
from decimal import Decimal

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from hotel.forms import BookingForm, PaymentForm
from hotel.models import AuditLog, Booking, Guest, Payment, Room, User
from hotel.reporting import build_report


@override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=['testserver', 'localhost'])
class ManagementUpdatesTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('owner', role='owner')
        self.admin = User.objects.create_user('admin', role='admin')
        self.room = Room.objects.create(name='Тест', capacity=4, daily_rate=Decimal('1234.56'))
        self.guest = Guest.objects.create(full_name='Тестовый гость', phone='123')
        self.start = timezone.make_aware(datetime(2026, 10, 6, 14))
        self.end = timezone.make_aware(datetime(2026, 10, 9, 12))

    def data(self, **extra):
        result = {'room': self.room.pk, 'primary_guest': self.guest.pk,
                  'check_in': timezone.localtime(self.start).strftime('%Y-%m-%dT%H:%M'),
                  'check_out': timezone.localtime(self.end).strftime('%Y-%m-%dT%H:%M'),
                  'guest_count': 2, 'payment_method': 'cash'}
        result.update(extra)
        return result

    def create_booking(self, **extra):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('booking_create'), self.data(**extra))
        self.assertEqual(response.status_code, 302)
        return Booking.objects.get()

    def test_rate_times_local_days_and_client_total_ignored(self):
        booking = self.create_booking(total_cost='0.01', linen_sets='999', companions=[self.guest.pk])
        self.assertEqual(booking.stay_days, 3)
        self.assertEqual(booking.daily_rate, Decimal('1234.56'))
        self.assertEqual(booking.total_cost, Decimal('3703.68'))
        self.assertEqual(booking.linen_sets, 0)
        self.assertEqual(booking.additional_guests.count(), 0)

    def test_saved_rate_survives_room_price_changes_and_extension(self):
        booking = self.create_booking()
        self.room.daily_rate = Decimal('9999')
        self.room.save()
        new_end = timezone.localtime(self.end + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M')
        response = self.client.post(reverse('booking_edit', args=[booking.pk]), self.data(check_out=new_end))
        self.assertEqual(response.status_code, 302)
        booking.refresh_from_db()
        self.assertEqual(booking.daily_rate, Decimal('1234.56'))
        self.assertEqual(booking.total_cost, Decimal('6172.80'))

    def test_room_change_uses_new_rate(self):
        booking = self.create_booking()
        other = Room.objects.create(name='Другой', capacity=4, daily_rate=Decimal('2000'))
        response = self.client.post(reverse('booking_edit', args=[booking.pk]), self.data(room=other.pk))
        self.assertEqual(response.status_code, 302)
        booking.refresh_from_db()
        self.assertEqual(booking.total_cost, Decimal('6000'))

    def test_legacy_total_preserved_on_comment_edit(self):
        booking = Booking.objects.create(room=self.room, primary_guest=self.guest, check_in=self.start, check_out=self.end, total_cost=Decimal('999.99'))
        self.client.force_login(self.admin)
        response = self.client.post(reverse('booking_edit', args=[booking.pk]), self.data(comment='Новый комментарий'))
        self.assertEqual(response.status_code, 302)
        booking.refresh_from_db()
        self.assertEqual(booking.total_cost, Decimal('999.99'))
        self.assertIsNone(booking.daily_rate)

    def test_same_day_minimum_and_midnight_rule(self):
        for start, end, expected in [(self.start, self.start + timedelta(hours=1), 1),
                                     (self.start, self.start + timedelta(hours=25), 1),
                                     (self.start, self.start + timedelta(days=2), 2)]:
            form = BookingForm(data=self.data(check_in=timezone.localtime(start).strftime('%Y-%m-%dT%H:%M'), check_out=timezone.localtime(end).strftime('%Y-%m-%dT%H:%M')))
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.instance.total_cost, self.room.daily_rate * expected)

    def test_other_payment_and_prepayment_require_comment(self):
        booking = self.create_booking()
        payment = Payment(booking=booking, amount=Decimal('10'), method='other', created_by=self.admin)
        with self.assertRaises(ValidationError):
            payment.save()
        form = PaymentForm(data={'booking': booking.pk, 'amount': '10', 'method': 'other', 'paid_at': '2026-10-06T12:00', 'comment': '   '})
        self.assertFalse(form.is_valid())
        self.assertIn('comment', form.errors)
        form = BookingForm(data=self.data(check_in='2026-10-10T14:00', check_out='2026-10-11T12:00', prepayment='10', payment_method='other'))
        self.assertFalse(form.is_valid())
        self.assertIn('payment_comment', form.errors)
        payment.comment = 'Оплата через сервис'
        payment.save()
        self.assertEqual(booking.paid, Decimal('10'))

    def test_other_prepayment_comment_saved(self):
        booking = self.create_booking(prepayment='10', payment_method='other', payment_comment='Сертификат')
        self.assertEqual(booking.payments.get().comment, 'Сертификат')

    def test_admin_cannot_create_promote_or_modify_owner(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('user_create')).status_code, 403)
        self.assertEqual(self.client.post(reverse('user_create'), {'username': 'evil', 'role': 'owner'}).status_code, 403)
        self.assertEqual(self.client.post(reverse('user_edit', args=[self.owner.pk]), {'role': 'admin'}).status_code, 403)
        self.assertEqual(self.client.get(reverse('user_edit', args=[self.admin.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('user_edit', args=[self.admin.pk]), {'role': 'owner', 'is_superuser': 'on', 'is_active': 'on'}).status_code, 403)
        self.assertEqual(self.client.get(reverse('user_password_reset', args=[self.admin.pk])).status_code, 403)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, 'admin')
        self.assertFalse(self.admin.is_superuser)
        settings_response = self.client.get(reverse('settings'))
        self.assertNotContains(settings_response, 'Пользователи и доступ')
        self.assertNotContains(settings_response, self.owner.username)
        self.owner.is_active = False
        self.owner.save()
        self.assertEqual(self.client.get(reverse('user_edit', args=[self.owner.pk])).status_code, 403)

    def test_admin_cannot_create_edit_or_delete_rooms(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('room_create')).status_code, 403)
        self.assertEqual(self.client.post(reverse('room_create'), {'name': 'Лишний'}).status_code, 403)
        self.assertEqual(self.client.get(reverse('room_edit', args=[self.room.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('room_edit', args=[self.room.pk]), {'name': 'Изменён'}).status_code, 403)
        self.assertEqual(self.client.get(reverse('room_delete', args=[self.room.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('room_delete', args=[self.room.pk])).status_code, 403)
        self.assertEqual(Room.objects.count(), 1)

    def test_guest_can_be_deleted_from_guest_list(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('guests'))
        self.assertContains(response, reverse('guest_delete', args=[self.guest.pk]))
        response = self.client.post(reverse('guest_delete', args=[self.guest.pk]))
        self.assertRedirects(response, reverse('guests'))
        self.assertFalse(Guest.objects.filter(pk=self.guest.pk).exists())

    def test_owner_can_reset_admin_password(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse('user_password_reset', args=[self.admin.pk]), {
            'new_password1': 'Brand-new-Strong-872!',
            'new_password2': 'Brand-new-Strong-872!',
        })
        self.assertRedirects(response, reverse('settings'))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password('Brand-new-Strong-872!'))
        self.assertTrue(AuditLog.objects.filter(action='Смена пароля пользователя', user=self.owner).exists())

    def test_owner_can_create_admin_and_view_audit(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse('user_create'), {'username': 'second', 'role': 'admin', 'is_active': 'on', 'password1': 'Strong-test-pass-349!', 'password2': 'Strong-test-pass-349!'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.get(username='second').role, 'admin')
        self.assertEqual(self.client.get(reverse('audit')).status_code, 200)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('audit')).status_code, 403)
        self.assertNotContains(self.client.get(reverse('settings')), reverse('audit'))

    def test_passport_digits_and_pairs(self):
        for fields in [{'passport_series': '123', 'passport_number': '123456'},
                       {'passport_series': 'abcd', 'passport_number': '123456'},
                       {'passport_series': '1234'}, {'passport_number': '123456'}]:
            form = BookingForm(data=self.data(**fields))
            self.assertFalse(form.is_valid(), fields)
        self.create_booking(passport_series='0012', passport_number='000123', passport_issued_by='Тестовое подразделение')
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.passport_series, '0012')
        self.assertEqual(self.guest.passport_number, '000123')
        self.assertFalse(AuditLog.objects.filter(description__contains='000123').exists())

    def test_passport_ciphertext_in_database_and_roundtrip(self):
        self.guest.passport_series, self.guest.passport_number = '0012', '000123'
        self.guest.passport_issued_by = 'Тестовый орган'
        self.guest.save()
        with connection.cursor() as cursor:
            cursor.execute('SELECT passport_series, passport_number, passport_issued_by FROM hotel_guest WHERE id = %s', [self.guest.pk])
            stored = cursor.fetchone()
        for token in stored:
            self.assertTrue(token.startswith('enc:v1:'))
        self.assertNotIn('000123', stored[1])
        self.guest.refresh_from_db()
        self.assertEqual(self.guest.passport_number, '000123')
        self.guest.save()
        with connection.cursor() as cursor:
            cursor.execute('SELECT passport_number FROM hotel_guest WHERE id = %s', [self.guest.pk])
            self.assertNotEqual(cursor.fetchone()[0], stored[1])
        with override_settings(PASSPORT_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            with self.assertRaises(ImproperlyConfigured):
                Guest.objects.get(pk=self.guest.pk)

    def test_corrupt_ciphertext_fails_closed(self):
        with connection.cursor() as cursor:
            cursor.execute('UPDATE hotel_guest SET passport_number = %s WHERE id = %s', ['enc:v1:corrupt', self.guest.pk])
        with self.assertRaises(ImproperlyConfigured):
            Guest.objects.get(pk=self.guest.pk)

    def test_passport_endpoint_requires_login_and_disables_cache(self):
        url = reverse('guest_passport', args=[self.guest.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(url)['Cache-Control'], 'private, no-store')

    def test_payments_group_without_losing_transactions(self):
        booking = self.create_booking()
        for amount in ['100.01', '299.99']:
            Payment.objects.create(booking=booking, amount=amount, method='cash', created_by=self.admin)
        response = self.client.get(reverse('payments'))
        self.assertEqual(len(response.context['bookings']), 1)
        self.assertEqual(response.context['bookings'][0].paid, Decimal('400.00'))
        self.assertEqual(response.context['bookings'][0].payments.count(), 2)
        self.assertContains(response, 'Поступления и редактирование')

    def test_statistics_actual_stays_and_payment_boundaries(self):
        booking = self.create_booking()
        booking.status = 'completed'
        booking.actual_check_in = timezone.make_aware(datetime(2026, 10, 6, 0, 0))
        booking.actual_check_out = timezone.make_aware(datetime(2026, 10, 7, 12))
        booking.save()
        for when, amount in [(timezone.make_aware(datetime(2026, 10, 5, 23, 59)), '10'),
                             (timezone.make_aware(datetime(2026, 10, 6, 0, 0)), '100.01'),
                             (timezone.make_aware(datetime(2026, 10, 6, 23, 59)), '200.02'),
                             (timezone.make_aware(datetime(2026, 10, 7, 0, 0)), '20')]:
            Payment.objects.create(booking=booking, amount=amount, paid_at=when, method='cash', created_by=self.admin)
        Booking.objects.create(room=self.room, primary_guest=self.guest, check_in=self.start, check_out=self.end, total_cost=Decimal('1000'), status='cancelled', actual_check_in=booking.actual_check_in, guest_count=4)
        report = build_report('day', date(2026, 10, 6))
        self.assertEqual(report['income'], Decimal('300.03'))
        self.assertEqual(report['payment_count'], 2)
        self.assertEqual(report['visitors'], 2)
        self.assertEqual(report['stay_count'], 1)
        self.assertEqual(report['departure_count'], 0)
        self.assertEqual(report['chart'][0]['visitors'], 2)
        self.assertEqual(build_report('week', date(2026, 10, 6))['income'], Decimal('330.03'))
        self.assertEqual(build_report('month', date(2026, 10, 6))['income'], Decimal('330.03'))
        self.assertEqual(build_report('all', date(2026, 10, 6))['income'], Decimal('330.03'))

    def test_statistics_calendar_periods_and_empty_data(self):
        for period, day, start, last in [('week', date(2026, 1, 1), date(2025, 12, 29), date(2026, 1, 4)),
                                        ('month', date(2028, 2, 3), date(2028, 2, 1), date(2028, 2, 29))]:
            report = build_report(period, day)
            self.assertEqual(report['start'], start)
            self.assertEqual(report['last_day'], last)
            self.assertEqual(report['income'], Decimal('0'))
            self.assertEqual(report['visitors'], 0)
        self.client.force_login(self.admin)
        for period in ['day', 'week', 'month', 'all', 'invalid']:
            self.assertEqual(self.client.get(reverse('statistics'), {'period': period}).status_code, 200)
