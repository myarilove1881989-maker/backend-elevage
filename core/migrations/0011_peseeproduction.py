import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0010_generaliser_consommation_aliment"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PeseeProduction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("pesee_at", models.DateTimeField(default=timezone.now)),
                ("nombre_animaux_peses", models.PositiveIntegerField()),
                ("poids_total_kg", models.DecimalField(decimal_places=3, max_digits=12)),
                ("note", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="pesees_production_creees", to=settings.AUTH_USER_MODEL)),
                ("exploitation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pesees_production", to="core.exploitation")),
                ("lot", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pesees_production", to="core.lot")),
            ],
            options={"ordering": ("pesee_at", "id")},
        ),
        migrations.AddConstraint(
            model_name="peseeproduction",
            constraint=models.CheckConstraint(
                condition=models.Q(("nombre_animaux_peses__gt", 0)),
                name="pesee_animaux_gt_zero",
            ),
        ),
        migrations.AddConstraint(
            model_name="peseeproduction",
            constraint=models.CheckConstraint(
                condition=models.Q(("poids_total_kg__gt", 0)),
                name="pesee_poids_gt_zero",
            ),
        ),
        migrations.AddIndex(
            model_name="peseeproduction",
            index=models.Index(fields=["lot", "pesee_at"], name="core_pesee_lot_id_at_idx"),
        ),
    ]
