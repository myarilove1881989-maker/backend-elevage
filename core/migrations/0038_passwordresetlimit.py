from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('core', '0037_offline_business_revocation')]
    operations = [migrations.CreateModel(
        name='PasswordResetLimit',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('key', models.CharField(max_length=40, unique=True)),
            ('count', models.PositiveIntegerField(default=0)),
            ('expires_at', models.DateTimeField(db_index=True)),
        ],
    ), migrations.CreateModel(
        name='PasswordResetEmail',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('encrypted_code', models.BinaryField()),
            ('code', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to='core.passwordresetcode')),
        ],
    )]
