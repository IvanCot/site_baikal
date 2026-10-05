from pathlib import Path
from PIL import Image, UnidentifiedImageError
from django.conf import settings
from django.core.exceptions import ValidationError


def validate_document(file):
    if file.size > settings.MAX_DOCUMENT_SIZE:
        raise ValidationError('Размер каждого документа не должен превышать 10 МБ.')
    if file.size == 0:
        raise ValidationError('Нельзя загрузить пустой файл.')
    extension = Path(file.name).suffix.lower()
    if extension not in {'.jpg', '.jpeg', '.png', '.webp', '.pdf'}:
        raise ValidationError('Допустимы JPG, PNG, WEBP и PDF. Другие типы файлов запрещены.')
    try:
        if extension == '.pdf':
            if not file.read(5).startswith(b'%PDF-'):
                raise ValidationError('Файл не является корректным PDF.')
        else:
            with Image.open(file) as image:
                expected = {'.jpg': 'JPEG', '.jpeg': 'JPEG', '.png': 'PNG', '.webp': 'WEBP'}
                if image.format != expected[extension]:
                    raise ValidationError('Содержимое изображения не соответствует расширению.')
                image.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError, ValueError):
        raise ValidationError('Не удалось прочитать изображение. Выберите корректный файл.')
    finally:
        file.seek(0)
