"""Создаёт локальный .env со случайными секретами, не перезаписывая существующий."""
import secrets
from pathlib import Path

root = Path(__file__).resolve().parent.parent
destination = root / '.env'
if destination.exists():
    print('.env уже существует; изменения не внесены.')
else:
    content = (root / '.env.example').read_text(encoding='utf-8')
    content = content.replace('SECRET_KEY=\n', f'SECRET_KEY={secrets.token_urlsafe(48)}\n')
    content = content.replace('POSTGRES_PASSWORD=\n', f'POSTGRES_PASSWORD={secrets.token_urlsafe(32)}\n')
    with destination.open('x', encoding='utf-8', newline='\n') as file:
        file.write(content)
    print('Создан .env со случайными секретами. Значения не выводятся в журнал.')
