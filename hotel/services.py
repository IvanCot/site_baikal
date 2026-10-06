from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .models import AuditLog, Booking, Room


def price_booking(booking, previous=None):
    """Сохраняет тариф брони; старые ручные суммы остаются без изменений."""
    same_room = previous and previous.room_id == booking.room_id
    if same_room and previous.check_in == booking.check_in and previous.check_out == booking.check_out:
        booking.daily_rate, booking.total_cost = previous.daily_rate, previous.total_cost
        return
    booking.daily_rate = previous.daily_rate if same_room and previous.daily_rate is not None else booking.room.daily_rate
    booking.total_cost = booking.daily_rate * booking.stay_days


def audit(user, action, obj, description):
    AuditLog.objects.create(user=user, action=action, object_type=obj.__class__.__name__,
                            object_id=str(obj.pk), description=description)


@transaction.atomic
def transition(booking_id, action, user):
    room_id = Booking.objects.values_list('room_id', flat=True).get(pk=booking_id)
    room = Room.objects.select_for_update().get(pk=room_id)
    booking = Booking.objects.select_for_update().get(pk=booking_id)
    if booking.room_id != room.pk:
        raise ValidationError('Бронирование изменилось. Обновите страницу и повторите действие.')
    now = timezone.now()
    if action == 'checkin' and booking.status == Booking.Status.RESERVED:
        if not room.active or room.service_status != Room.Service.READY:
            raise ValidationError('Перед заселением номер должен быть включён и подготовлен.')
        if room.bookings.filter(status=Booking.Status.OCCUPIED).exclude(pk=booking.pk).exists():
            raise ValidationError('В номере ещё проживает другой гость. Сначала выполните выселение.')
        if not booking.check_in <= now < booking.check_out:
            raise ValidationError('Заселение доступно в плановый интервал. Для раннего заезда измените время бронирования.')
        booking.status = Booking.Status.OCCUPIED
        booking.actual_check_in = now
        label = 'Заселение'
    elif action == 'checkout' and booking.status == Booking.Status.OCCUPIED:
        booking.status = Booking.Status.COMPLETED
        booking.actual_check_out = now
        room.service_status = Room.Service.CLEANING
        room.save(update_fields=['service_status'])
        label = 'Выселение'
        audit(user, 'Уборка', room, f'Номер {room.name}: требуется уборка после выселения.')
    elif action == 'cancel' and booking.status == Booking.Status.RESERVED and user.is_manager:
        booking.status = Booking.Status.CANCELLED
        label = 'Отмена'
    else:
        raise ValidationError('Действие недоступно для текущего статуса бронирования.')
    booking.save()
    audit(user, label, booking, f'{label}: бронирование №{booking.number}, номер {room.name}.')
    return booking
