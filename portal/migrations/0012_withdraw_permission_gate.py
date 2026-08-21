from django.db import migrations


def clear_permission_flags(apps, schema_editor):
    """Turn the permission-before-work gate off on every process type.

    Withdrawn after the demo. The column and the historical auth stages are
    kept so the sign-off history on tasks that went through the gate still
    reads correctly; only the flag that arms it is cleared.
    """
    ProcessType = apps.get_model('portal', 'ProcessType')
    ProcessType.objects.filter(requires_authorisation=True).update(requires_authorisation=False)


def rearm(apps, schema_editor):
    """Nothing to restore: which types were flagged is not recorded anywhere."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('portal', '0011_alter_approval_id_alter_attachment_id_and_more'),
    ]

    operations = [
        migrations.RunPython(clear_permission_flags, rearm),
    ]
