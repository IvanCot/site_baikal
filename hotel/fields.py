from django.db import models


class EncryptedCharField(models.CharField):
    """Тип столбца для исторической миграции 0005, без шифрования.

    Миграция 0006 удаляет старые столбцы без чтения их содержимого.
    """

    def get_internal_type(self):
        return 'TextField'
