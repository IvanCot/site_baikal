from decimal import Decimal
from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError
from .models import Booking, Guest, Payment, Room, User


class DateTimeInput(forms.DateTimeInput):
    input_type = 'datetime-local'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, format='%Y-%m-%dT%H:%M', **kwargs)


class BookingForm(forms.ModelForm):
    primary_guest = forms.ModelChoiceField(label='Основной гость из справочника', queryset=Guest.objects.all(), required=False)
    new_guest_name = forms.CharField(label='ФИО нового гостя', max_length=200, required=False)
    new_guest_phone = forms.CharField(label='Телефон нового гостя', max_length=40, required=False)
    prepayment = forms.DecimalField(label='Предоплата, ₽', max_digits=12, decimal_places=2, min_value=Decimal('0'), initial=0, required=False)
    payment_method = forms.ChoiceField(label='Способ предоплаты', choices=Payment.Method.choices)
    payment_comment = forms.CharField(label='Комментарий к предоплате', required=False, widget=forms.Textarea(attrs={'rows': 2}))

    class Meta:
        model = Booking
        fields = ['room', 'primary_guest', 'check_in', 'check_out', 'guest_count', 'comment']
        widgets = {'check_in': DateTimeInput(), 'check_out': DateTimeInput(), 'comment': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.original = Booking.objects.filter(pk=self.instance.pk).first() if self.instance.pk else None
        self.fields['room'].queryset = Room.objects.filter(active=True).exclude(service_status=Room.Service.UNAVAILABLE)
        if self.instance.pk:
            self.fields['room'].queryset = Room.objects.filter(active=True) | Room.objects.filter(pk=self.instance.room_id)
            del self.fields['prepayment']
            del self.fields['payment_method']
            del self.fields['payment_comment']

    def clean(self):
        data = super().clean()
        guest = data.get('primary_guest')
        name, phone = data.get('new_guest_name'), data.get('new_guest_phone')
        if not guest and not (name and phone):
            self.add_error('primary_guest', 'Выберите существующего гостя или укажите ФИО и телефон нового.')
        if guest and (name or phone):
            self.add_error('new_guest_name', 'Выберите существующего гостя или заполните нового, без одновременного ввода.')
        if data.get('prepayment') and data.get('payment_method') == 'other' and not data.get('payment_comment', '').strip():
            self.add_error('payment_comment', 'Для способа «Другое» укажите, как была выполнена оплата.')
        return data

    def _post_clean(self):
        room = self.cleaned_data.get('room')
        start, end = self.cleaned_data.get('check_in'), self.cleaned_data.get('check_out')
        if room and start and end and end > start:
            from .services import price_booking
            self.instance.room, self.instance.check_in, self.instance.check_out = room, start, end
            price_booking(self.instance, self.original)
        super()._post_clean()


class RoomForm(forms.ModelForm):
    class Meta:
        model = Room
        fields = ['name', 'beds', 'capacity', 'daily_rate', 'comment', 'active', 'service_status']
        widgets = {'comment': forms.Textarea(attrs={'rows': 3})}

    def clean_capacity(self):
        capacity = self.cleaned_data['capacity']
        if self.instance.pk and self.instance.bookings.filter(status__in=['reserved', 'occupied'], guest_count__gt=capacity).exists():
            raise ValidationError('Есть действующие брони с большим количеством гостей. Сначала измените эти брони.')
        return capacity

    def clean(self):
        data = super().clean()
        if self.instance.pk and self.instance.bookings.filter(status__in=['reserved', 'occupied']).exists():
            if data.get('active') is False:
                self.add_error('active', 'Сначала завершите или отмените действующие бронирования номера.')
            if data.get('service_status') == Room.Service.UNAVAILABLE:
                self.add_error('service_status', 'Нельзя закрыть номер с действующими бронированиями.')
        return data


class GuestForm(forms.ModelForm):
    class Meta:
        model = Guest
        fields = ['full_name', 'phone', 'email', 'comment']
        widgets = {'comment': forms.Textarea(attrs={'rows': 3})}


class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ['booking', 'paid_at', 'amount', 'method', 'comment']
        widgets = {'paid_at': DateTimeInput(), 'comment': forms.Textarea(attrs={'rows': 3})}


class CreateUserForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ['username', 'first_name', 'last_name', 'role', 'is_active']


class EditUserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'role', 'is_active']

    def clean_role(self):
        if self.instance.is_superuser:
            return User.Role.OWNER
        return self.cleaned_data['role']
