from django.db import migrations
from django.db.models import Count


def seed(apps,schema_editor):
    Category=apps.get_model('core','CategorieDepense')
    Binding=apps.get_model('core','SupplyCategoryBinding')
    # One-time recognition of the historical seed only. Never used by forms/API.
    farms=Category.objects.filter(nom='Aliment').values('exploitation_id').annotate(n=Count('pk')).filter(n=1)
    for farm in farms.iterator():
        category=Category.objects.get(nom='Aliment',exploitation_id=farm['exploitation_id'])
        Binding.objects.get_or_create(category_id=category.pk,defaults={'purpose':'FEED'})


class Migration(migrations.Migration):
    dependencies=[('core','0042_supply_foundation')]
    operations=[migrations.RunPython(seed,migrations.RunPython.noop)]
