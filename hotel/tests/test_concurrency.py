from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, connection, connections
from django.test import TransactionTestCase
from django.utils import timezone
from hotel.models import Booking, Guest, Room, User
from hotel.services import transition


@skipUnless(connection.vendor == 'postgresql', 'Параллельные транзакции требуют PostgreSQL')
class ConcurrentBookingTests(TransactionTestCase):
    def setUp(self):
        self.room = Room.objects.create(name='№11', capacity=2)
        self.guest = Guest.objects.create(full_name='Тестовый гость', phone='123')
        self.employee = User.objects.create(username='staff')
        self.now = timezone.now()

    def test_two_simultaneous_reservations_only_one_succeeds(self):
        barrier = Barrier(2)
        room_id, guest_id, now = self.room.pk, self.guest.pk, self.now
        def create_booking():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                Booking.objects.create(room_id=room_id, primary_guest_id=guest_id,
                    check_in=now - timedelta(hours=1), check_out=now + timedelta(days=1),
                    guest_count=1, total_cost=Decimal('1000'))
                return 'saved'
            except (ValidationError, IntegrityError):
                return 'conflict'
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(create_booking) for _ in range(2)]
            results = [f.result(timeout=15) for f in futures]
        self.assertCountEqual(results, ['saved', 'conflict'])
        self.assertEqual(Booking.objects.count(), 1)

    def test_repeated_simultaneous_checkin_changes_status_once(self):
        booking = Booking.objects.create(room=self.room, primary_guest=self.guest,
            check_in=self.now - timedelta(hours=1), check_out=self.now + timedelta(days=1), guest_count=1, total_cost=Decimal('1000'))
        barrier = Barrier(2)
        booking_id, user_id = booking.pk, self.employee.pk
        def checkin():
            close_old_connections()
            try:
                actor = User.objects.get(pk=user_id)
                barrier.wait(timeout=10)
                transition(booking_id, 'checkin', actor)
                return 'changed'
            except ValidationError:
                return 'rejected'
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(checkin) for _ in range(2)]
            results = [f.result(timeout=15) for f in futures]
        self.assertCountEqual(results, ['changed', 'rejected'])
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'occupied')
