from django.core.management.base import BaseCommand
from core.models import User

class Command(BaseCommand):
    def handle(self, *args, **kwargs):
        user, created = User.objects.get_or_create(username="admin")

        user.is_staff = True
        user.is_superuser = True
        user.set_password("admin123")
        user.save()

        print("✅ Admin prêt (admin / admin123)")