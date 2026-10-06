from decimal import Decimal
from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class UpgradeMigrationTests(TransactionTestCase):
    def test_roles_and_existing_records_are_preserved(self):
        old_target = [('hotel', '0004_booking_one_actual_stay_per_room')]
        new_target = [('hotel', '0005_owner_roles_rates_passports')]
        executor = MigrationExecutor(connection)
        executor.migrate(old_target)
        try:
            apps = executor.loader.project_state(old_target).apps
            User, Guest, Room, Booking, BookingGuest, Document = [apps.get_model('hotel', name) for name in ['User', 'Guest', 'Room', 'Booking', 'BookingGuest', 'Document']]
            password = make_password('Preserved-pass-348!')
            owner = User.objects.create(username='previous-admin', role='admin', password=password)
            staff = User.objects.create(username='previous-staff', role='employee')
            superuser = User.objects.create(username='previous-super', role='employee', is_superuser=True)
            guest = Guest.objects.create(full_name='Исторический гость', phone='123')
            room = Room.objects.create(name='Исторический номер')
            booking = Booking.objects.create(number='LEGACY0001', room=room, primary_guest=guest, check_in=timezone.now(), check_out=timezone.now() + timedelta(days=2), total_cost=Decimal('123.45'), linen_sets=3)
            link = BookingGuest.objects.create(booking=booking, guest=guest)
            doc = Document.objects.create(booking=booking, file='documents/old.pdf', original_name='old.pdf', uploaded_by=owner)
            ids = owner.pk, staff.pk, superuser.pk, booking.pk, link.pk, doc.pk
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(new_target)
        apps = executor.loader.project_state(new_target).apps
        User, Booking, BookingGuest, Document = [apps.get_model('hotel', name) for name in ['User', 'Booking', 'BookingGuest', 'Document']]
        owner_id, staff_id, super_id, booking_id, link_id, doc_id = ids
        self.assertEqual(User.objects.get(pk=owner_id).role, 'owner')
        self.assertEqual(User.objects.get(pk=owner_id).password, password)
        self.assertEqual(User.objects.get(pk=staff_id).role, 'admin')
        self.assertEqual(User.objects.get(pk=super_id).role, 'owner')
        saved = Booking.objects.get(pk=booking_id)
        self.assertEqual(saved.total_cost, Decimal('123.45'))
        self.assertEqual(saved.linen_sets, 3)
        self.assertIsNone(saved.daily_rate)
        self.assertTrue(BookingGuest.objects.filter(pk=link_id).exists())
        self.assertEqual(Document.objects.get(pk=doc_id).file.name, 'documents/old.pdf')
