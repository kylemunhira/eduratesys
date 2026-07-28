from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Wipe all portal data and re-seed VAST AFRICA catalog."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default="admin123",
            help="Password for admin user (default: admin123)",
        )

    def handle(self, *args, **options):
        self.stdout.write("Flushing database...")
        call_command("flush", interactive=False)
        self.stdout.write(self.style.WARNING("Database cleared."))
        call_command("seed_demo", password=options["password"])
