from django.core.management.base import BaseCommand
from core.reset_mail import deliver_pending


class Command(BaseCommand):
    help = 'Deliver up to 100 queued password reset emails.'

    def handle(self, *args, **options):
        sent = deliver_pending()
        self.stdout.write(f'{sent} password reset emails delivered.')
