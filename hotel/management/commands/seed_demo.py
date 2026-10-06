from datetime import datetime, time, timedelta
from decimal import Decimal
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from hotel.models import AuditLog, Booking, Guest, Payment, Room, User


class Command(BaseCommand):
    help = 'Добавляет демонстрационные данные в пустую базу. Не создаёт пароли.'

    @transaction.atomic
    def handle(self, *args, **options):
        if Booking.objects.exists() or Room.objects.exists() or Guest.objects.exists():
            raise CommandError('Демо-данные добавляются только в пустую базу, чтобы не затронуть рабочие записи.')
        actor = User.objects.filter(is_active=True).filter(role='owner').first() or User.objects.filter(is_superuser=True).first()
        if not actor:
            raise CommandError('Сначала создайте владельца: python manage.py createsuperuser.')
        now = timezone.now()
        today = timezone.localdate()
        def at(offset, hour=14):
            return timezone.make_aware(datetime.combine(today + timedelta(days=offset), time(hour)))
        rooms = [Room.objects.create(name=name, beds=2, capacity=3, daily_rate=Decimal('5000')) for name in ['№11', '№12', 'Мансарда', '№14']]
        guests = [Guest.objects.create(full_name=name, phone=f'+7 900 000-00-0{i}', comment='Демонстрационная запись')
                  for i, name in enumerate(['Иванов Иван Петрович', 'Сидорова Анна Сергеевна', 'Орлов Михаил Андреевич', 'Петрова Елена Викторовна', 'Иванова Мария Петровна'], 1)]
        stay = Booking.objects.create(room=rooms[0], primary_guest=guests[0], check_in=at(-2), check_out=at(0, 23),
                                      guest_count=2, total_cost=Decimal('10000'), daily_rate=rooms[0].daily_rate,
                                      status='occupied', actual_check_in=at(-2), comment='Демонстрационное проживание.')
        arrival = Booking.objects.create(room=rooms[1], primary_guest=guests[1], check_in=at(0, 0), check_out=at(3, 12),
                                         guest_count=1, total_cost=Decimal('15000'), daily_rate=rooms[1].daily_rate, comment='Гость приезжает сегодня.')
        future = Booking.objects.create(room=rooms[2], primary_guest=guests[2], check_in=at(2), check_out=at(5, 12),
                                        guest_count=2, total_cost=Decimal('15000'), daily_rate=rooms[2].daily_rate)
        completed = Booking.objects.create(room=rooms[3], primary_guest=guests[3], check_in=at(-6), check_out=at(-3, 12),
                                           guest_count=1, total_cost=Decimal('15000'), daily_rate=rooms[3].daily_rate, status='completed',
                                           actual_check_in=at(-6), actual_check_out=at(-3, 12))
        rooms[3].service_status = 'cleaning'
        rooms[3].save()
        for booking, amount, method in [(stay, '5000', 'transfer'), (arrival, '12000', 'card'), (future, '6000', 'transfer'), (completed, '4000', 'transfer'), (completed, '5000', 'cash')]:
            Payment.objects.create(booking=booking, amount=Decimal(amount), method=method, created_by=actor, paid_at=now)
        AuditLog.objects.create(user=actor, action='Демо-данные', object_type='System', object_id='0', description='Созданы вымышленные данные для знакомства с системой.')
        self.stdout.write(self.style.SUCCESS('Демо-данные добавлены: 4 номера, 5 гостей, 4 бронирования и 5 платежей.'))
