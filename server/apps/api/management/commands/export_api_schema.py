from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Generate the shared API contract, or check it without changing files."

    def add_arguments(self, parser):
        parser.add_argument('--check', action='store_true')
        parser.add_argument('--output', type=Path, default=settings.REPOSITORY_DIR / 'contracts/openapi.yaml')

    def handle(self, *args, **options):
        destination = options['output']
        with TemporaryDirectory(prefix='kt-schema-') as directory:
            generated = Path(directory) / 'openapi.yaml'
            call_command('spectacular', file=str(generated), validate=True)
            content = generated.read_text(encoding='utf-8')
        if options['check']:
            if not destination.exists() or destination.read_text(encoding='utf-8') != content:
                raise CommandError('API contract is stale. Run python manage.py export_api_schema.')
            self.stdout.write('API contract matches the backend.')
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding='utf-8', newline='\n')
            self.stdout.write(str(destination))
