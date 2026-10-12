import time

from django.core.management.base import BaseCommand, CommandError
from apps.analytics.student_comparisons import run_comparison_jobs


class Command(BaseCommand):
    help = 'Generate shared student class-task comparisons (one worker slot).'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--limit', type=int, default=1)
        parser.add_argument('--concurrency', type=int, default=1)
        parser.add_argument('--poll-seconds', type=float, default=2)

    def handle(self, *args, **options):
        from apps.analytics.runtime_pressure import runtime_pressure

        if options['concurrency'] != 1:
            raise CommandError('Student comparisons require --concurrency=1.')
        while True:
            pressure = runtime_pressure()
            if pressure['mode'] == 'capture_emergency':
                result = {'completed': 0, 'failed': 0}
            else:
                result = run_comparison_jobs(limit=1)
            if options['once']:
                self.stdout.write(str(result))
                return
            delay = pressure['retry_after_seconds'] if pressure['mode'] == 'capture_emergency' else options['poll_seconds']
            time.sleep(max(0.1, delay))
