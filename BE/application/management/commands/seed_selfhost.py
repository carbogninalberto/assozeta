from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Deprecated compatibility command; software subscriptions are no longer required."

    def handle(self, *args, **options):
        # Older deployment scripts may still call this command during upgrades.
        self.stdout.write("No software billing reference data is required.")
