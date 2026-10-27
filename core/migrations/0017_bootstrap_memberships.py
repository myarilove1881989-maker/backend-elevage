from django.db import migrations


def bootstrap(apps, schema_editor):
    Farm = apps.get_model('core', 'Exploitation')
    User = apps.get_model('core', 'User')
    Member = apps.get_model('core', 'ExploitationMembership')
    alias = schema_editor.connection.alias
    for farm in Farm.objects.using(alias).all().iterator():
        # Owner determined solely from the recorded proprietor. Do not rewrite User.
        Member.objects.using(alias).get_or_create(user_id=farm.proprietaire_id,
            exploitation_id=farm.pk, defaults={'role': 'OWNER'})
        for user in User.objects.using(alias).filter(exploitation_id=farm.pk).exclude(
                pk=farm.proprietaire_id).iterator():
            Member.objects.using(alias).get_or_create(user_id=user.pk, exploitation_id=farm.pk,
                                                     defaults={'role': 'OPERATEUR'})


class Migration(migrations.Migration):
    dependencies = [('core', '0016_client_created_by_depense_created_by_and_more')]
    # Reversing must not remove real users' memberships created after bootstrap.
    operations = [migrations.RunPython(bootstrap, migrations.RunPython.noop)]
