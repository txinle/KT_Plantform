from django.core.management.base import BaseCommand

from apps.api.shell import production_asset_urls


class Command(BaseCommand):
    help = "Verify that the Vite manifest references existing hashed assets."

    def handle(self, *args, **options):
        assets = production_asset_urls()
        count = len(assets["scripts"]) + len(assets["styles"])
        self.stdout.write(self.style.SUCCESS(f"vue_assets={count}"))
