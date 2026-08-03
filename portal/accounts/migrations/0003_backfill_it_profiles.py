# Generated manually — backfill IT profiles for existing superusers.

from django.db import migrations


def forwards(apps, schema_editor):
    User = apps.get_model("auth", "User")
    UserProfile = apps.get_model("accounts", "UserProfile")
    for user in User.objects.filter(is_superuser=True):
        profile, created = UserProfile.objects.get_or_create(
            user=user,
            defaults={"role": "IT"},
        )
        if not created and not profile.role:
            profile.role = "IT"
            profile.save(update_fields=["role"])


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_userprofile"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
