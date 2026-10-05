import os
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from hotel.models import User


class Command(BaseCommand):
    help = 'Создаёт администратора из ADMIN_USERNAME / ADMIN_PASSWORD только при отсутствии такого логина.'

    def handle(self, *args, **options):
        username, password = os.getenv('ADMIN_USERNAME'), os.getenv('ADMIN_PASSWORD')
        if not username:
            return
        if User.objects.filter(username=username).exists():
            self.stdout.write('Указанная учётная запись уже существует; пароль и права не изменены.')
            return
        if not password:
            raise CommandError('Для создания администратора укажите ADMIN_PASSWORD.')
        user = User(username=username, role=User.Role.ADMIN, email=os.getenv('ADMIN_EMAIL', ''))
        try:
            validate_password(password, user)
            user.full_clean(exclude=['password'])
        except ValidationError as error:
            raise CommandError(' '.join(error.messages))
        user.set_password(password)
        user.is_staff = True
        user.is_superuser = True
        user.save()
        self.stdout.write(self.style.SUCCESS('Администратор создан.'))
