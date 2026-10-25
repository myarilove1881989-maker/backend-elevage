from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import PasswordResetCode, PasswordResetLimit


class Command(BaseCommand):
    help = 'Remove expired reset codes and abuse counters.'

    def handle(self, *args, **options):
        now = timezone.now()
        PasswordResetLimit.objects.filter(expires_at__lt=now).delete()
        PasswordResetCode.objects.filter(expires_at__lt=now - timedelta(days=1)).delete()
        self.stdout.write('Expired password reset data removed.')
