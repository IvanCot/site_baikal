from decimal import Decimal
from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError
from .models import Booking, Guest, Payment, Room, User
from .validators import validate_document


class DateTimeInput(forms.DateTimeInput):
    input_type = 'datetime-local'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, format='%Y-%m-%dT%H:%M', **kwargs)


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('widget', MultipleFileInput(attrs={'accept': '.jpg,.jpeg,.png,.webp,.pdf'}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else [data] if data else []
        if len(files) > 5:
            raise ValidationError('За один раз можно загрузить не более 5 документов.')
        result = []
        for file in files:
            file = super().clean(file, initial)
            validate_document(file)
            result.append(file)
        return result


class BookingForm(forms.ModelForm):
    primary_guest = forms.ModelChoiceField(label='Основной гость из справочника', queryset=Guest.objects.all(), required=False)
    new_guest_name = forms.CharField(label='ФИО нового гостя', max_length=200, required=False)
    new_guest_phone = forms.CharField(label='Телефон нового гостя', max_length=40, required=False)
    companions = forms.ModelMultipleChoiceField(label='Сопровождающие из справочника', queryset=Guest.objects.all(), required=False)
    prepayment = forms.DecimalField(label='Предоплата, ₽', max_digits=12, decimal_places=2, min_value=Decimal('0'), initial=0, required=False)
    payment_method = forms.ChoiceField(label='Способ предоплаты', choices=Payment.Method.choices)
    documents = MultipleFileField(label='Документы', required=False, help_text='До 5 файлов по 10 МБ. JPG, PNG, WEBP, PDF.')
    document_comment = forms.CharField(label='Комментарий к документам', max_length=400, required=False)

    class Meta:
        model = Booking
        fields = ['room', 'primary_guest', 'check_in', 'check_out', 'guest_count', 'total_cost', 'linen_sets', 'comment']
        widgets = {'check_in': DateTimeInput(), 'check_out': DateTimeInput(), 'comment': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['room'].queryset = Room.objects.filter(active=True).exclude(service_status=Room.Service.UNAVAILABLE)
        if self.instance.pk:
            self.fields['room'].queryset = Room.objects.filter(active=True) | Room.objects.filter(pk=self.instance.room_id)
            self.fields['companions'].initial = self.instance.additional_guests.all()
            del self.fields['prepayment']
            del self.fields['payment_method']

    def clean(self):
        data = super().clean()
        guest = data.get('primary_guest')
        name, phone = data.get('new_guest_name'), data.get('new_guest_phone')
        if not guest and not (name and phone):
            self.add_error('primary_guest', 'Выберите существующего гостя или укажите ФИО и телефон нового.')
        if guest and (name or phone):
            self.add_error('new_guest_name', 'Выберите существующего гостя или заполните нового, без одновременного ввода.')
        companions = data.get('companions', [])
        if guest and guest in companions:
            self.add_error('companions', 'Основной гость уже включён в бронирование.')
        if data.get('guest_count') and len(companions) + 1 > data['guest_count']:
            self.add_error('guest_count', 'Количество гостей меньше числа выбранных людей.')
        return data


class RoomForm(forms.ModelForm):
    class Meta:
        model = Room
        fields = ['name', 'beds', 'capacity', 'comment', 'active', 'service_status']
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


class DocumentForm(forms.Form):
    documents = MultipleFileField(label='Документы', required=True, help_text='JPG, PNG, WEBP, PDF — не более 10 МБ на файл.')
    comment = forms.CharField(label='Комментарий', max_length=400, required=False)

    def clean_documents(self):
        files = self.cleaned_data['documents']
        if not files:
            raise ValidationError('Выберите документ.')
        return files


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
            return User.Role.ADMIN
        return self.cleaned_data['role']
