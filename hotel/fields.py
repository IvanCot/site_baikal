from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models


class EncryptedCharField(models.CharField):
    """Короткий текст в форме, аутентифицированный шифротекст в БД."""
    prefix = 'enc:v1:'

    def get_internal_type(self):
        return 'TextField'

    def cipher(self):
        try:
            return Fernet(settings.PASSPORT_ENCRYPTION_KEY.encode('ascii'))
        except (ValueError, UnicodeError):
            raise ImproperlyConfigured('Некорректный PASSPORT_ENCRYPTION_KEY.') from None

    def from_db_value(self, value, expression, connection):
        if value in (None, ''):
            return value
        if not value.startswith(self.prefix):
            raise ImproperlyConfigured('Паспортное поле содержит незашифрованные данные.')
        try:
            return self.cipher().decrypt(value[len(self.prefix):].encode('ascii')).decode('utf-8')
        except (InvalidToken, UnicodeError):
            raise ImproperlyConfigured('Не удалось расшифровать паспортные данные. Проверьте ключ шифрования.') from None

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value in (None, ''):
            return value
        return self.prefix + self.cipher().encrypt(value.encode('utf-8')).decode('ascii')
