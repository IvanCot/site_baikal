from django.db import migrations


def delete_passport_files(apps, schema_editor):
    Document = apps.get_model('hotel', 'Document')
    storage = Document._meta.get_field('file').storage
    database = schema_editor.connection.alias
    # Не читаем Guest через ORM: для удаления шифротекста ключ не нужен.
    for name in Document.objects.using(database).values_list('file', flat=True).iterator():
        if name:
            storage.delete(name)

    # Убираем также оставшиеся файлы незавершённых старых загрузок.
    def clear_directory(directory):
        if not storage.exists(directory):
            return
        directories, files = storage.listdir(directory)
        for name in files:
            storage.delete(f'{directory}/{name}')
        for name in directories:
            clear_directory(f'{directory}/{name}')

    clear_directory('documents')
    AuditLog = apps.get_model('hotel', 'AuditLog')
    AuditLog.objects.using(database).filter(object_type='Document').delete()
    AuditLog.objects.using(database).filter(action='Паспортные данные').delete()


class Migration(migrations.Migration):
    dependencies = [('hotel', '0005_owner_roles_rates_passports')]
    operations = [
        # Обратная миграция создаёт пустую схему, удалённые данные не восстанавливает.
        migrations.RunPython(delete_passport_files, migrations.RunPython.noop),
        migrations.RemoveField(model_name='guest', name='passport_series'),
        migrations.RemoveField(model_name='guest', name='passport_number'),
        migrations.RemoveField(model_name='guest', name='passport_issued_by'),
        migrations.RemoveField(model_name='guest', name='passport_issued_on'),
        migrations.DeleteModel(name='Document'),
    ]
