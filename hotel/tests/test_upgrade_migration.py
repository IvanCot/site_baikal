from decimal import Decimal
from datetime import timedelta
import tempfile
from pathlib import Path

from django.contrib.auth.hashers import make_password
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings
from django.utils import timezone


class UpgradeMigrationTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.media_root = Path(directory.name)
        media = override_settings(MEDIA_ROOT=directory.name)
        media.enable()
        self.addCleanup(media.disable)

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

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

    def test_remove_passports_preserves_business_data_and_deletes_files(self):
        old_target = [('hotel', '0005_owner_roles_rates_passports')]
        executor = MigrationExecutor(connection)
        executor.migrate(old_target)
        apps = executor.loader.project_state(old_target).apps
        User, Guest, Room, Booking, Payment, Document, AuditLog = [
            apps.get_model('hotel', name)
            for name in ['User', 'Guest', 'Room', 'Booking', 'Payment', 'Document', 'AuditLog']
        ]
        owner = User.objects.create(username='owner-preserved', role='owner', password='unchanged')
        guest = Guest.objects.create(full_name='Гость без паспорта', phone='123',
                                     passport_series='enc:v1:unreadable',
                                     passport_number='enc:v1:unreadable',
                                     passport_issued_by='enc:v1:unreadable',
                                     passport_issued_on=timezone.localdate())
        room = Room.objects.create(name='Номер без паспорта', daily_rate=Decimal('1000'))
        booking = Booking.objects.create(number='PRESERVED1', room=room, primary_guest=guest,
                                         check_in=timezone.now(), check_out=timezone.now() + timedelta(days=2),
                                         total_cost=Decimal('2000'), daily_rate=Decimal('1000'))
        payment = Payment.objects.create(booking=booking, amount=Decimal('500'), method='cash',
                                         paid_at=timezone.now(), created_by=owner)
        for filename, relation in [('guest.jpg', {'guest': guest}), ('booking.pdf', {'booking': booking})]:
            path = self.media_root / 'documents' / filename
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b'old private file')
            doc = Document.objects.create(file=f'documents/{filename}', original_name=filename,
                                          uploaded_by=owner, **relation)
            AuditLog.objects.create(user=owner, action='Загрузка документа', object_type='Document',
                                    object_id=str(doc.pk), description='Прикреплён документ')
        (self.media_root / 'documents' / 'orphan.jpg').write_bytes(b'orphan')
        AuditLog.objects.create(user=owner, action='Паспортные данные', object_type='Guest',
                                object_id=str(guest.pk), description='Обновлён паспорт')
        AuditLog.objects.create(user=owner, action='Создание бронирования', object_type='Booking',
                                object_id=str(booking.pk), description='Сохранено')
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        apps = executor.loader.project_state(executor.loader.graph.leaf_nodes()).apps
        self.assertEqual(apps.get_model('hotel', 'Guest').objects.get(pk=guest.pk).phone, '123')
        self.assertEqual(apps.get_model('hotel', 'Booking').objects.get(pk=booking.pk).total_cost, Decimal('2000'))
        self.assertEqual(apps.get_model('hotel', 'Payment').objects.get(pk=payment.pk).amount, Decimal('500'))
        self.assertEqual(apps.get_model('hotel', 'User').objects.get(pk=owner.pk).password, 'unchanged')
        self.assertEqual(apps.get_model('hotel', 'AuditLog').objects.count(), 1)
        self.assertEqual(list((self.media_root / 'documents').rglob('*')), [])
        self.assertNotIn('hotel_document', connection.introspection.table_names())
        with connection.cursor() as cursor:
            columns = connection.introspection.get_table_description(cursor, 'hotel_guest')
        self.assertFalse(any(column.name.startswith('passport_') for column in columns))
