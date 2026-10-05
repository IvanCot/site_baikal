from django.db import migrations


def add_guards(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute('CREATE EXTENSION IF NOT EXISTS btree_gist')
    schema_editor.execute("""
        ALTER TABLE hotel_booking ADD CONSTRAINT booking_no_overlap
        EXCLUDE USING gist (
            room_id WITH =,
            tstzrange(check_in, check_out, '[)') WITH &&
        ) WHERE (status IN ('reserved', 'occupied'))
    """)
    schema_editor.execute("""
        CREATE FUNCTION prevent_booking_delete() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Удаление бронирований запрещено. Используйте отмену.';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER booking_keep_history BEFORE DELETE ON hotel_booking
        FOR EACH ROW EXECUTE FUNCTION prevent_booking_delete();
    """)


def remove_guards(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute('DROP TRIGGER IF EXISTS booking_keep_history ON hotel_booking')
    schema_editor.execute('DROP FUNCTION IF EXISTS prevent_booking_delete()')
    schema_editor.execute('ALTER TABLE hotel_booking DROP CONSTRAINT IF EXISTS booking_no_overlap')


class Migration(migrations.Migration):
    dependencies = [('hotel', '0001_initial')]
    operations = [migrations.RunPython(add_guards, remove_guards)]
