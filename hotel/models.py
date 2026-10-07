import uuid
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Q, Sum
from django.utils import timezone


class HotelUserManager(UserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault('role', 'owner')
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = 'admin', 'Администратор'
        OWNER = 'owner', 'Владелец'
    role = models.CharField('Роль', max_length=12, choices=Role.choices, default=Role.ADMIN)
    objects = HotelUserManager()

    @property
    def is_manager(self):
        return self.is_active and self.role in self.Role.values

    @property
    def is_owner(self):
        return self.is_active and self.role == self.Role.OWNER


class Room(models.Model):
    class Service(models.TextChoices):
        READY = 'ready', 'Готов к заселению'
        CLEANING = 'cleaning', 'Требуется уборка'
        UNAVAILABLE = 'unavailable', 'Недоступен'
    name = models.CharField('Название / номер', max_length=80, unique=True)
    beds = models.PositiveSmallIntegerField('Количество кроватей', default=1, validators=[MinValueValidator(1)])
    capacity = models.PositiveSmallIntegerField('Максимум гостей', default=2, validators=[MinValueValidator(1)])
    daily_rate = models.DecimalField('Тариф за сутки, ₽', max_digits=12, decimal_places=2, default=Decimal('0'), validators=[MinValueValidator(Decimal('0'))])
    comment = models.TextField('Комментарий', blank=True)
    active = models.BooleanField('Номер включён', default=True)
    service_status = models.CharField('Служебное состояние', max_length=16, choices=Service.choices, default=Service.READY)

    class Meta:
        ordering = ['name']
        constraints = [models.CheckConstraint(condition=Q(beds__gt=0) & Q(capacity__gt=0), name='room_positive_capacity'),
                       models.CheckConstraint(condition=Q(daily_rate__gte=0), name='room_nonnegative_rate')]

    def __str__(self):
        return self.name

    @property
    def current_booking(self):
        return self.bookings.filter(status=Booking.Status.OCCUPIED).select_related('primary_guest').first()

    @property
    def state(self):
        if self.current_booking:
            return 'occupied', 'Занят'
        if not self.active or self.service_status == self.Service.UNAVAILABLE:
            return 'unavailable', 'Недоступен'
        if self.service_status == self.Service.CLEANING:
            return 'cleaning', 'Требуется уборка'
        now = timezone.now()
        if self.bookings.filter(status=Booking.Status.RESERVED, check_in__lte=now, check_out__gt=now).exists():
            return 'reserved', 'Забронирован'
        return 'free', 'Свободен'


class Guest(models.Model):
    full_name = models.CharField('ФИО', max_length=200)
    phone = models.CharField('Телефон', max_length=40)
    email = models.EmailField('Электронная почта', blank=True)
    comment = models.TextField('Комментарий', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['full_name']

    def __str__(self):
        return f'{self.full_name} · {self.phone}'


class BookingQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError('Бронирования нельзя удалять. Используйте отмену.')


class Booking(models.Model):
    class Status(models.TextChoices):
        RESERVED = 'reserved', 'Забронировано'
        OCCUPIED = 'occupied', 'Проживает'
        COMPLETED = 'completed', 'Завершено'
        CANCELLED = 'cancelled', 'Отменено'
    number = models.CharField('Номер бронирования', max_length=12, unique=True, editable=False)
    room = models.ForeignKey(Room, on_delete=models.PROTECT, related_name='bookings', verbose_name='Номер')
    primary_guest = models.ForeignKey(Guest, on_delete=models.PROTECT, related_name='primary_bookings', verbose_name='Основной гость')
    additional_guests = models.ManyToManyField(Guest, through='BookingGuest', related_name='additional_bookings', verbose_name='Сопровождающие')
    check_in = models.DateTimeField('Плановый заезд')
    check_out = models.DateTimeField('Плановый выезд')
    actual_check_in = models.DateTimeField('Фактический заезд', null=True, blank=True)
    actual_check_out = models.DateTimeField('Фактический выезд', null=True, blank=True)
    guest_count = models.PositiveSmallIntegerField('Количество гостей', default=1, validators=[MinValueValidator(1)])
    total_cost = models.DecimalField('Стоимость проживания, ₽', max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0'))])
    daily_rate = models.DecimalField('Сохранённый тариф за сутки, ₽', max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal('0'))])
    linen_sets = models.PositiveSmallIntegerField('Комплекты белья', default=0)
    comment = models.TextField('Комментарий', blank=True)
    status = models.CharField('Статус', max_length=12, choices=Status.choices, default=Status.RESERVED)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = BookingQuerySet.as_manager()

    class Meta:
        ordering = ['-check_in']
        indexes = [models.Index(fields=['room', 'check_in', 'check_out']), models.Index(fields=['status', 'check_in'])]
        constraints = [
            models.CheckConstraint(condition=Q(check_out__gt=models.F('check_in')), name='booking_correct_interval'),
            models.CheckConstraint(condition=Q(guest_count__gt=0) & Q(total_cost__gte=0), name='booking_positive_values'),
            models.UniqueConstraint(fields=['room'], condition=Q(status='occupied'), name='one_actual_stay_per_room',
                                    violation_error_message='В номере уже проживает другой гость.'),
        ]

    def __str__(self):
        return f'Бронирование №{self.number}'

    def clean(self):
        errors = {}
        if self.check_in and self.check_out and self.check_out <= self.check_in:
            errors['check_out'] = 'Выезд должен быть позже заезда.'
        if self.room_id and self.guest_count and self.guest_count > self.room.capacity:
            errors['guest_count'] = 'Количество гостей превышает вместимость номера.'
        if self.status in [self.Status.RESERVED, self.Status.OCCUPIED] and self.room_id:
            if not self.room.active or self.room.service_status == Room.Service.UNAVAILABLE:
                errors['room'] = 'Номер отключён или недоступен.'
            if self.status == self.Status.OCCUPIED and Booking.objects.filter(room_id=self.room_id, status=self.Status.OCCUPIED).exclude(pk=self.pk).exists():
                errors['room'] = 'В номере уже проживает другой гость. Сначала выполните выселение.'
            if self.check_in and self.check_out:
                conflict = Booking.objects.filter(
                    room_id=self.room_id, status__in=[self.Status.RESERVED, self.Status.OCCUPIED],
                    check_in__lt=self.check_out, check_out__gt=self.check_in,
                ).exclude(pk=self.pk)
                if conflict.exists():
                    errors['check_in'] = 'На выбранное время номер уже забронирован. Измените даты или номер.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        with transaction.atomic():
            room_ids = {self.room_id}
            if self.pk:
                old_room = Booking.objects.filter(pk=self.pk).values_list('room_id', flat=True).first()
                if old_room:
                    room_ids.add(old_room)
            list(Room.objects.select_for_update().filter(pk__in=room_ids).order_by('pk'))
            self.room = Room.objects.get(pk=self.room_id)
            if not self.number:
                self.number = uuid.uuid4().hex[:10].upper()
            self.full_clean()
            return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Бронирования нельзя удалять. Используйте отмену.')

    @property
    def stay_days(self):
        return max(1, (timezone.localtime(self.check_out).date() - timezone.localtime(self.check_in).date()).days)

    @property
    def paid(self):
        if 'payments' in getattr(self, '_prefetched_objects_cache', {}):
            return sum((p.amount for p in self.payments.all()), Decimal('0.00'))
        return self.payments.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    @property
    def balance(self):
        return self.total_cost - self.paid

    @property
    def financial_code(self):
        paid = self.paid
        if paid > self.total_cost:
            return 'overpaid'
        if paid == self.total_cost:
            return 'paid'
        if paid == 0:
            return 'unpaid'
        return 'partial'

    @property
    def financial_status(self):
        return {'unpaid': 'Не оплачено', 'partial': 'Частично оплачено',
                'paid': 'Оплачено полностью', 'overpaid': 'Переплата'}[self.financial_code]


class BookingGuest(models.Model):
    booking = models.ForeignKey(Booking, on_delete=models.PROTECT)
    guest = models.ForeignKey(Guest, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['booking', 'guest'], name='unique_booking_guest')]


class Payment(models.Model):
    class Method(models.TextChoices):
        CASH = 'cash', 'Наличные'
        CARD = 'card', 'Карта'
        TRANSFER = 'transfer', 'Перевод'
        OTHER = 'other', 'Другое'
    booking = models.ForeignKey(Booking, on_delete=models.PROTECT, related_name='payments', verbose_name='Бронирование')
    paid_at = models.DateTimeField('Дата и время', default=timezone.now)
    amount = models.DecimalField('Сумма, ₽', max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    method = models.CharField('Способ оплаты', max_length=10, choices=Method.choices)
    comment = models.TextField('Комментарий', blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name='Добавил')

    class Meta:
        ordering = ['-paid_at']
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name='payment_positive_amount')]

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self):
        if self.method == self.Method.OTHER and not self.comment.strip():
            raise ValidationError({'comment': 'Для способа «Другое» укажите, как была выполнена оплата.'})


def document_path(instance, filename):
    """Совместимость с исторической миграцией 0001; новые файлы не принимаются."""
    return f'documents/{uuid.uuid4().hex}{Path(filename).suffix.lower()}'


class AuditLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    action = models.CharField('Действие', max_length=80)
    object_type = models.CharField(max_length=50)
    object_id = models.CharField(max_length=50)
    timestamp = models.DateTimeField('Время', auto_now_add=True)
    description = models.TextField('Описание')

    class Meta:
        ordering = ['-timestamp']
