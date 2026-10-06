from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import TruncDate, TruncMonth
from django.utils import timezone

from .models import Booking, Payment


def midnight(day):
    return timezone.make_aware(datetime.combine(day, time.min))


def build_report(period, anchor):
    periods = {'day': 'День', 'week': 'Неделя', 'month': 'Месяц', 'all': 'Всё время'}
    if period not in periods:
        period = 'day'
    start = {'day': anchor, 'week': anchor - timedelta(days=anchor.weekday()), 'month': anchor.replace(day=1), 'all': None}[period]
    if period == 'week':
        end = start + timedelta(days=7)
    elif period == 'month':
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    else:
        end = anchor + timedelta(days=1)
    payments = Payment.objects.all()
    stays = Booking.objects.filter(status__in=['occupied', 'completed'], actual_check_in__isnull=False)
    departures = Booking.objects.filter(status='completed', actual_check_out__isnull=False)
    if start:
        payments = payments.filter(paid_at__gte=midnight(start), paid_at__lt=midnight(end))
        stays = stays.filter(actual_check_in__gte=midnight(start), actual_check_in__lt=midnight(end))
        departures = departures.filter(actual_check_out__gte=midnight(start), actual_check_out__lt=midnight(end))
    income = payments.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    visitors = stays.aggregate(total=Sum('guest_count'))['total'] or 0
    truncation = TruncMonth if period == 'all' else TruncDate
    money_rows = payments.order_by().annotate(bucket=truncation('paid_at')).values('bucket').annotate(total=Sum('amount'))
    visitor_rows = stays.order_by().annotate(bucket=truncation('actual_check_in')).values('bucket').annotate(total=Sum('guest_count'))
    def normalize(bucket):
        return timezone.localtime(bucket).date() if isinstance(bucket, datetime) else bucket
    money = {normalize(row['bucket']): row['total'] for row in money_rows}
    people = {normalize(row['bucket']): row['total'] for row in visitor_rows}
    if start:
        buckets = [start + timedelta(days=i) for i in range((end - start).days)]
    elif money or people:
        first, last = min(money.keys() | people.keys()), max(money.keys() | people.keys())
        buckets = []
        current = first
        while current <= last:
            buckets.append(current)
            current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
    else:
        buckets = []
    chart = [{'label': day.strftime('%m.%Y' if period == 'all' else '%d.%m'), 'income': money.get(day, Decimal('0.00')), 'visitors': people.get(day, 0)} for day in buckets]
    max_income = max((row['income'] for row in chart), default=Decimal('0')) or Decimal('1')
    max_visitors = max((row['visitors'] for row in chart), default=0) or 1
    for row in chart:
        row['money_width'] = round(row['income'] / max_income * 100, 2)
        row['visitor_width'] = round(row['visitors'] / max_visitors * 100, 2)
    methods = payments.order_by().values('method').annotate(total=Sum('amount'), count=Count('pk'))
    return {'period': period, 'periods': periods.items(), 'anchor': anchor, 'start': start,
            'last_day': end - timedelta(days=1), 'income': income, 'payment_count': payments.count(),
            'visitors': visitors, 'stay_count': stays.count(), 'departure_count': departures.count(),
            'chart': chart, 'methods': [{'label': Payment.Method(row['method']).label, **row} for row in methods],
            'average_payment': income / payments.count() if payments.exists() else Decimal('0')}
