from django.apps import AppConfig

class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        from django.conf import settings

        try:
            from django.contrib.auth import get_user_model
            User = get_user_model()

            if not User.objects.filter(username="admin").exists():
                User.objects.create_superuser(
                    username="admin",
                    password="admin123",
                    email="admin@test.com"
                )
                print("🔥 Superuser créé automatiquement")
        except Exception as e:
            print("Erreur création superuser:", e)