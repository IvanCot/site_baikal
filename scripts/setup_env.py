"""Создаёт .env или добавляет недостающий ключ паспортов, сохраняя настройки."""
import secrets
import base64
from pathlib import Path

root = Path(__file__).resolve().parent.parent
destination = root / '.env'
if destination.exists():
    content = destination.read_text(encoding='utf-8')
    if not any(line.strip().startswith('PASSPORT_ENCRYPTION_KEY=') and line.split('=', 1)[1].strip() for line in content.splitlines()):
        key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('ascii')
        content = '\n'.join(line for line in content.splitlines() if not line.strip().startswith('PASSPORT_ENCRYPTION_KEY='))
        destination.write_text(content + f'\nPASSPORT_ENCRYPTION_KEY={key}\n', encoding='utf-8')
        print('Добавлен отдельный ключ шифрования паспортов. Остальные настройки сохранены; секреты не выводятся.')
    else:
        print('.env уже существует; настройки и ключ шифрования сохранены.')
else:
    content = (root / '.env.example').read_text(encoding='utf-8')
    content = content.replace('SECRET_KEY=\n', f'SECRET_KEY={secrets.token_urlsafe(48)}\n')
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('ascii')
    content = content.replace('PASSPORT_ENCRYPTION_KEY=\n', f'PASSPORT_ENCRYPTION_KEY={key}\n')
    content = content.replace('POSTGRES_PASSWORD=\n', f'POSTGRES_PASSWORD={secrets.token_urlsafe(32)}\n')
    with destination.open('x', encoding='utf-8', newline='\n') as file:
        file.write(content)
    print('Создан .env со случайными секретами. Значения не выводятся в журнал.')
