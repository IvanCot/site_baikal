from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import DecimalField, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Coalesce
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import (BookingForm, CreateUserForm, DocumentForm, EditUserForm, GuestForm,
                    PaymentForm, RoomForm)
from .models import AuditLog, Booking, BookingGuest, Document, Guest, Payment, Room, User
from .permissions import manager_required
from .services import audit, transition


def local_midnight(day):
    return timezone.make_aware(datetime.combine(day, time.min))


def parsed_date(value, default=None):
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return default


def page(request, queryset):
    return Paginator(queryset, 30).get_page(request.GET.get('page'))


def validation_message(error):
    return ' '.join(error.messages)


@login_required
def dashboard(request):
    today = timezone.localdate()
    start, end = local_midnight(today), local_midnight(today + timedelta(days=1))
    bookings = Booking.objects.select_related('room', 'primary_guest')
    arrivals = bookings.filter(status='reserved', check_in__gte=start, check_in__lt=end).order_by('check_in')
    departures = bookings.filter(status='occupied', check_out__gte=start, check_out__lt=end).order_by('check_out')
    stays = bookings.filter(status='occupied')
    room_rows = [{'room': room, 'state': room.state, 'booking': room.current_booking} for room in Room.objects.filter(active=True)]
    counts = {
        'arrivals': arrivals.count(), 'departures': departures.count(),
        'guests': stays.aggregate(total=Sum('guest_count'))['total'] or 0,
        'free': sum(row['state'][0] == 'free' for row in room_rows),
        'occupied': sum(row['state'][0] == 'occupied' for row in room_rows),
        'cleaning': sum(row['room'].service_status == 'cleaning' for row in room_rows),
    }
    overdue = stays.filter(check_out__lt=timezone.now())
    return render(request, 'dashboard.html', {'title': 'Сегодня в гостевом доме', 'arrivals': arrivals,
        'departures': departures, 'room_rows': room_rows, 'counts': counts, 'overdue': overdue})


@login_required
def calendar(request):
    start = parsed_date(request.GET.get('start'), timezone.localdate())
    end = parsed_date(request.GET.get('end'), start + timedelta(days=13))
    if end < start:
        end = start
    if (end - start).days > 60:
        end = start + timedelta(days=60)
        messages.info(request, 'Календарь показывает до 61 дня за один раз.')
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    bookings = list(Booking.objects.filter(status__in=['reserved', 'occupied'], check_in__lt=local_midnight(end + timedelta(days=1)),
                    check_out__gt=local_midnight(start)).select_related('primary_guest', 'room').order_by('check_in'))
    rows = []
    for room in Room.objects.filter(active=True):
        cells = []
        for day in days:
            matches = [b for b in bookings if b.room_id == room.pk and b.check_in < local_midnight(day + timedelta(days=1)) and b.check_out > local_midnight(day)]
            cells.append({'day': day, 'bookings': matches})
        rows.append({'room': room, 'cells': cells})
    span = len(days)
    return render(request, 'calendar.html', {'title': 'Календарь номеров', 'days': days, 'rows': rows,
        'start': start, 'end': end, 'previous': start - timedelta(days=span), 'next': start + timedelta(days=span),
        'previous_end': end - timedelta(days=span), 'next_end': end + timedelta(days=span)})


@login_required
def booking_list(request, history=False):
    qs = Booking.objects.select_related('room', 'primary_guest').prefetch_related('payments', 'documents')
    if history:
        qs = qs.filter(status__in=['completed', 'cancelled'])
    q = request.GET.get('q', '').strip()[:200]
    if q:
        qs = qs.filter(Q(primary_guest__full_name__icontains=q) | Q(primary_guest__phone__icontains=q) |
                       Q(room__name__icontains=q) | Q(number__icontains=q) | Q(additional_guests__full_name__icontains=q) |
                       Q(additional_guests__phone__icontains=q)).distinct()
    status = request.GET.get('status', '')
    if status in Booking.Status.values:
        qs = qs.filter(status=status)
    room = request.GET.get('room', '')
    if room.isdigit():
        qs = qs.filter(room_id=room)
    start, end = parsed_date(request.GET.get('start')), parsed_date(request.GET.get('end'))
    if start:
        qs = qs.filter(check_out__gt=local_midnight(start))
    if end:
        qs = qs.filter(check_in__lt=local_midnight(end + timedelta(days=1)))
    finance = request.GET.get('finance', '')
    if request.user.is_manager and finance:
        payment_sum = Payment.objects.filter(booking_id=OuterRef('pk')).order_by().values('booking_id').annotate(total=Sum('amount')).values('total')
        qs = qs.annotate(paid_total=Coalesce(Subquery(payment_sum), Value(Decimal('0')), output_field=DecimalField(max_digits=12, decimal_places=2)))
        filters = {'unpaid': Q(paid_total=0) & Q(total_cost__gt=0),
                   'partial': Q(paid_total__gt=0) & Q(paid_total__lt=F('total_cost')),
                   'paid': Q(paid_total=F('total_cost')), 'overpaid': Q(paid_total__gt=F('total_cost'))}
        if finance in filters:
            qs = qs.filter(filters[finance])
    return render(request, 'bookings.html', {'title': 'История бронирований' if history else 'Бронирования',
        'bookings': page(request, qs.order_by('-check_in', '-pk')), 'rooms': Room.objects.all(), 'statuses': Booking.Status.choices, 'q': q, 'history': history})


@login_required
def booking_detail(request, pk):
    booking = get_object_or_404(Booking.objects.select_related('room', 'primary_guest').prefetch_related('additional_guests'), pk=pk)
    return render(request, 'booking_detail.html', {'title': str(booking), 'booking': booking})


def save_documents(files, user, guest=None, booking=None, comment='', saved=None):
    saved = saved if saved is not None else []
    for file in files:
        doc = Document(guest=guest, booking=booking, original_name=Path(file.name).name[:240],
                       uploaded_by=user, comment=comment)
        doc.file.save(file.name, file, save=False)
        saved.append(doc.file)
        doc.save()
        audit(user, 'Загрузка документа', doc, 'Прикреплён защищённый документ.')


@manager_required
def booking_edit(request, pk=None):
    booking = get_object_or_404(Booking, pk=pk) if pk else None
    if booking and booking.status not in ['reserved', 'occupied']:
        messages.error(request, 'Завершённые и отменённые бронирования доступны только для просмотра.')
        return redirect('booking_detail', pk=pk)
    day = parsed_date(request.GET.get('date'), timezone.localdate())
    room_id = request.GET.get('room', '')
    initial = {'room': room_id if room_id.isdigit() else None,
               'check_in': timezone.make_aware(datetime.combine(day, time(14))),
               'check_out': timezone.make_aware(datetime.combine(day + timedelta(days=1), time(12)))}
    form = BookingForm(request.POST or None, request.FILES or None, instance=booking, initial=initial)
    if request.method == 'POST' and form.is_valid():
        saved_files = []
        try:
            with transaction.atomic():
                obj = form.save(commit=False)
                ids = {obj.room_id}
                if pk:
                    ids.add(Booking.objects.get(pk=pk).room_id)
                list(Room.objects.select_for_update().filter(pk__in=ids).order_by('pk'))
                if pk:
                    before = Booking.objects.select_for_update().get(pk=pk)
                    if before.status not in ['reserved', 'occupied']:
                        raise ValidationError('Статус бронирования изменился. Обновите страницу.')
                    obj.status, obj.actual_check_in, obj.actual_check_out = before.status, before.actual_check_in, before.actual_check_out
                if not form.cleaned_data['primary_guest']:
                    guest, created = Guest.objects.get_or_create(full_name=form.cleaned_data['new_guest_name'], phone=form.cleaned_data['new_guest_phone'])
                    obj.primary_guest = guest
                    if created:
                        audit(request.user, 'Создание гостя', guest, 'Создан гость при бронировании.')
                obj.save()
                BookingGuest.objects.filter(booking=obj).delete()
                BookingGuest.objects.bulk_create([BookingGuest(booking=obj, guest=g) for g in form.cleaned_data['companions']])
                if not pk and form.cleaned_data.get('prepayment'):
                    payment = Payment.objects.create(booking=obj, amount=form.cleaned_data['prepayment'], method=form.cleaned_data['payment_method'], created_by=request.user)
                    audit(request.user, 'Добавление платежа', payment, f'Предоплата {payment.amount} ₽, бронирование №{obj.number}.')
                save_documents(form.cleaned_data['documents'], request.user, booking=obj,
                               comment=form.cleaned_data['document_comment'], saved=saved_files)
                description = f'Бронирование №{obj.number}: сохранено.'
                if pk:
                    changes = []
                    for field in ['room_id', 'check_in', 'check_out', 'guest_count', 'total_cost', 'linen_sets', 'primary_guest_id', 'comment']:
                        old, new = getattr(before, field), getattr(obj, field)
                        if old != new:
                            label = obj._meta.get_field(field.removesuffix('_id')).verbose_name
                            if isinstance(old, datetime):
                                old, new = timezone.localtime(old).strftime('%d.%m.%Y %H:%M'), timezone.localtime(new).strftime('%d.%m.%Y %H:%M')
                            if field == 'comment':
                                changes.append('Комментарий изменён')
                            else:
                                changes.append(f'{label}: {old} → {new}')
                    description = f'Бронирование №{obj.number}: ' + ('; '.join(changes) or 'обновлён состав гостей / документы')
                audit(request.user, 'Изменение бронирования' if pk else 'Создание бронирования', obj, description)
            messages.success(request, 'Бронирование сохранено.')
            return redirect('booking_detail', pk=obj.pk)
        except (ValidationError, IntegrityError) as error:
            for file in saved_files:
                file.delete(save=False)
            form.add_error(None, validation_message(error) if isinstance(error, ValidationError) else 'Номер уже забронирован другим пользователем. Выберите другой интервал.')
        except Exception:
            for file in saved_files:
                file.delete(save=False)
            raise
    return render(request, 'booking_form.html', {'title': 'Редактировать бронирование' if pk else 'Новое бронирование', 'form': form, 'booking': booking})


@login_required
@require_POST
def booking_action(request, pk, action):
    if action == 'cancel' and not request.user.is_manager:
        raise PermissionDenied
    get_object_or_404(Booking, pk=pk)
    try:
        transition(pk, action, request.user)
        messages.success(request, {'checkin': 'Гость заселён.', 'checkout': 'Гость выселен. Номер ожидает уборки.', 'cancel': 'Бронирование отменено.'}.get(action, 'Готово.'))
    except (ValidationError, IntegrityError) as error:
        messages.error(request, validation_message(error) if isinstance(error, ValidationError) else 'Не удалось изменить бронирование: конфликт дат.')
    return redirect('booking_detail', pk=pk)


@login_required
def rooms(request):
    return render(request, 'rooms.html', {'title': 'Номера', 'room_rows': [{'room': r, 'state': r.state} for r in Room.objects.all()]})


@manager_required
def room_edit(request, pk=None):
    obj = get_object_or_404(Room, pk=pk) if pk else None
    form = RoomForm(request.POST or None, instance=obj)
    if request.method == 'POST':
        with transaction.atomic():
            if pk:
                locked = Room.objects.select_for_update().get(pk=pk)
                form = RoomForm(request.POST, instance=locked)
            if form.is_valid():
                obj = form.save()
                audit(request.user, 'Изменение номера' if pk else 'Создание номера', obj,
                      f'Номер {obj.name}: вместимость {obj.capacity}; включён: {obj.active}; {obj.get_service_status_display()}.')
                messages.success(request, 'Номер сохранён.')
                return redirect('rooms')
    return render(request, 'form.html', {'title': 'Редактировать номер' if pk else 'Новый номер', 'form': form})


@login_required
@require_POST
def room_clean(request, pk):
    with transaction.atomic():
        room = get_object_or_404(Room.objects.select_for_update(), pk=pk)
        if room.service_status != Room.Service.CLEANING:
            messages.error(request, 'Этот номер не ожидает уборки.')
        else:
            room.service_status = Room.Service.READY
            room.save(update_fields=['service_status'])
            audit(request.user, 'Уборка завершена', room, f'Номер {room.name}: уборка завершена.')
            messages.success(request, 'Уборка завершена.')
    return redirect('rooms')


@manager_required
def room_delete(request, pk):
    room = get_object_or_404(Room, pk=pk)
    if request.method == 'POST':
        try:
            with transaction.atomic():
                audit(request.user, 'Удаление номера', room, f'Удалён номер {room.name}.')
                room.delete()
            messages.success(request, 'Номер удалён.')
        except ProtectedError:
            messages.error(request, 'У номера есть история бронирований. Отключите его в настройках номера.')
        return redirect('rooms')
    return render(request, 'confirm.html', {'title': f'Удалить номер {room.name}?', 'description': 'Номер с историей бронирований удалить нельзя. Его можно отключить.'})


@manager_required
def guests(request):
    qs = Guest.objects.all()
    q = request.GET.get('q', '').strip()[:200]
    if q:
        qs = qs.filter(Q(full_name__icontains=q) | Q(phone__icontains=q))
    return render(request, 'guests.html', {'title': 'Гости', 'guests': page(request, qs), 'q': q})


@manager_required
def guest_search(request):
    q = request.GET.get('q', '').strip()[:200]
    qs = Guest.objects.filter(Q(full_name__icontains=q) | Q(phone__icontains=q))[:20] if q else Guest.objects.none()
    return JsonResponse({'results': [{'id': g.pk, 'text': str(g)} for g in qs]})


@manager_required
def guest_edit(request, pk=None):
    obj = get_object_or_404(Guest, pk=pk) if pk else None
    form = GuestForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            obj = form.save()
            audit(request.user, 'Изменение гостя' if pk else 'Создание гостя', obj, 'Сохранена карточка гостя.')
        messages.success(request, 'Карточка гостя сохранена.')
        return redirect('guest_detail', pk=obj.pk)
    return render(request, 'form.html', {'title': 'Редактировать гостя' if pk else 'Новый гость', 'form': form})


@manager_required
def guest_detail(request, pk):
    guest = get_object_or_404(Guest, pk=pk)
    stays = Booking.objects.filter(Q(primary_guest=guest) | Q(additional_guests=guest)).distinct().select_related('room')
    return render(request, 'guest_detail.html', {'title': guest.full_name, 'guest': guest, 'stays': stays})


@manager_required
def guest_delete(request, pk):
    guest = get_object_or_404(Guest, pk=pk)
    if request.method == 'POST':
        try:
            with transaction.atomic():
                audit(request.user, 'Удаление гостя', guest, 'Удалена карточка гостя без истории.')
                guest.delete()
            messages.success(request, 'Гость удалён.')
        except ProtectedError:
            messages.error(request, 'У гостя есть бронирования или документы. Карточка сохраняется для истории.')
        return redirect('guests')
    return render(request, 'confirm.html', {'title': 'Удалить гостя?', 'description': 'Удаление возможно только при отсутствии бронирований и документов.'})


@manager_required
def payments(request):
    qs = Payment.objects.select_related('booking', 'booking__primary_guest', 'created_by')
    q = request.GET.get('q', '').strip()[:200]
    if q:
        qs = qs.filter(Q(booking__number__icontains=q) | Q(booking__primary_guest__full_name__icontains=q))
    return render(request, 'payments.html', {'title': 'Оплаты', 'payments': page(request, qs), 'q': q})


@manager_required
def payment_edit(request, pk=None):
    obj = get_object_or_404(Payment, pk=pk) if pk else None
    booking_id = request.GET.get('booking', '')
    form = PaymentForm(request.POST or None, instance=obj, initial={'booking': booking_id if booking_id.isdigit() else None})
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            if pk:
                before = Payment.objects.select_for_update().get(pk=pk)
            obj = form.save(commit=False)
            if pk:
                obj.created_by = before.created_by
            else:
                obj.created_by = request.user
            obj.save()
            description = f'Бронирование №{obj.booking.number}: {obj.amount} ₽, {obj.get_method_display()}.'
            if pk:
                description += f' Ранее: {before.amount} ₽, {before.get_method_display()}, {timezone.localtime(before.paid_at):%d.%m.%Y %H:%M}.'
            audit(request.user, 'Изменение платежа' if pk else 'Добавление платежа', obj, description)
        messages.success(request, 'Платёж сохранён.')
        return redirect('booking_detail', pk=obj.booking_id)
    return render(request, 'form.html', {'title': 'Редактировать платёж' if pk else 'Добавить платёж', 'form': form})


@manager_required
def payment_delete(request, pk):
    obj = get_object_or_404(Payment, pk=pk)
    if request.method == 'POST':
        with transaction.atomic():
            obj = get_object_or_404(Payment.objects.select_for_update(), pk=pk)
            booking_id = obj.booking_id
            audit(request.user, 'Удаление платежа', obj, f'Удалён ошибочный платёж {obj.amount} ₽, {obj.get_method_display()}, бронирование №{obj.booking.number}.')
            obj.delete()
        messages.success(request, 'Ошибочный платёж удалён. Баланс пересчитан.')
        return redirect('booking_detail', pk=booking_id)
    return render(request, 'confirm.html', {'title': 'Удалить ошибочный платёж?', 'description': 'Операция будет записана в журнал. Баланс бронирования изменится.'})


@manager_required
def document_upload(request, kind, pk):
    if kind not in ['guest', 'booking']:
        raise Http404
    obj = get_object_or_404(Guest if kind == 'guest' else Booking, pk=pk)
    form = DocumentForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        saved = []
        try:
            with transaction.atomic():
                save_documents(form.cleaned_data['documents'], request.user, comment=form.cleaned_data['comment'], saved=saved, **{kind: obj})
        except Exception:
            for file in saved:
                file.delete(save=False)
            raise
        messages.success(request, 'Документы загружены.')
        return redirect('guest_detail' if kind == 'guest' else 'booking_detail', pk=pk)
    return render(request, 'form.html', {'title': 'Прикрепить документы', 'form': form})


@manager_required
def document_download(request, pk):
    doc = get_object_or_404(Document, pk=pk)
    try:
        file = doc.file.open('rb')
    except (FileNotFoundError, ValueError):
        raise Http404
    audit(request.user, 'Скачивание документа', doc, 'Доступ к защищённому документу.')
    response = FileResponse(file, as_attachment=True, filename=doc.original_name, content_type='application/octet-stream')
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@manager_required
def settings_page(request):
    return render(request, 'settings.html', {'title': 'Настройки', 'users': User.objects.all().order_by('username')})


@manager_required
def user_edit(request, pk=None):
    obj = get_object_or_404(User, pk=pk) if pk else None
    form = (EditUserForm if pk else CreateUserForm)(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            managers = list(User.objects.select_for_update().filter(Q(role='admin') | Q(is_superuser=True), is_active=True).order_by('pk'))
            edited = form.save(commit=False)
            if pk and obj.pk == request.user.pk and not (edited.is_active and (edited.role == 'admin' or edited.is_superuser)):
                form.add_error(None, 'Нельзя отключить собственный доступ администратора.')
            elif pk and len(managers) == 1 and managers[0].pk == pk and not (edited.is_active and (edited.role == 'admin' or edited.is_superuser)):
                form.add_error(None, 'В системе должен оставаться активный администратор.')
            else:
                edited.save()
                audit(request.user, 'Изменение пользователя' if pk else 'Создание пользователя', edited,
                      f'Пользователь {edited.username}: {edited.get_role_display()}, активен: {edited.is_active}.')
                messages.success(request, 'Пользователь сохранён.')
                return redirect('settings')
    return render(request, 'form.html', {'title': 'Редактировать пользователя' if pk else 'Новый пользователь', 'form': form})


@manager_required
def audit_list(request):
    qs = AuditLog.objects.select_related('user')
    q = request.GET.get('q', '').strip()[:200]
    if q:
        qs = qs.filter(Q(action__icontains=q) | Q(description__icontains=q) | Q(user__username__icontains=q))
    return render(request, 'audit.html', {'title': 'Журнал действий', 'logs': page(request, qs), 'q': q})


def error403(request, exception):
    return render(request, 'error.html', {'title': 'Доступ запрещён', 'description': 'У вашей учётной записи нет прав для этой операции.'}, status=403)


def error404(request, exception):
    return render(request, 'error.html', {'title': 'Страница не найдена', 'description': 'Проверьте адрес или вернитесь на главную.'}, status=404)


def error500(request):
    return render(request, 'error.html', {'title': 'Не удалось выполнить запрос', 'description': 'Повторите попытку позже. Если ошибка повторяется, обратитесь к администратору.'}, status=500)


def csrf_failure(request, reason=''):
    return render(request, 'error.html', {'title': 'Не удалось подтвердить запрос',
                  'description': 'Обновите страницу и повторите действие. Проверьте, что cookies разрешены в браузере.'}, status=403)
