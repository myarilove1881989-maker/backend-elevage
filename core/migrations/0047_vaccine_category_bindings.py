from django.db import migrations
from django.db.models import Count

def seed(apps,schema_editor):
    Category=apps.get_model('core','CategorieDepense');Binding=apps.get_model('core','SupplyCategoryBinding')
    for farm in Category.objects.filter(nom='Vaccin').values('exploitation_id').annotate(n=Count('pk')).filter(n=1).iterator():
        category=Category.objects.get(nom='Vaccin',exploitation_id=farm['exploitation_id'])
        Binding.objects.get_or_create(category_id=category.pk,defaults={'purpose':'VACCINE'})

class Migration(migrations.Migration):
    dependencies=[('core','0046_vaccination_and_more')]
    operations=[migrations.RunPython(seed,migrations.RunPython.noop)]
