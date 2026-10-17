import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from apps.assessment.prepared_services import enqueue_preparation_repair, run_preparation_repair
from apps.experiments.models import ExperimentVersionParticipantSnapshot


class Command(BaseCommand):
    help = 'Rebuild one coalesced prepared participant from frozen facts.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--interval', type=int, default=5)
        parser.add_argument('--enqueue-task', type=int)
        parser.add_argument('--enqueue-only', action='store_true')

    def handle(self, *args, **options):
        if options['enqueue_only'] and not options['enqueue_task']:
            raise CommandError('--enqueue-only requires --enqueue-task.')
        if options['enqueue_task']:
            participants = ExperimentVersionParticipantSnapshot.objects.filter(
                class_task_id=options['enqueue_task']).exclude(
                sessions__started_at__isnull=False).exclude(sessions__prepared_artifact__ready=True)
            for participant in participants.only('pk', 'version_id').iterator(chunk_size=100):
                enqueue_preparation_repair(participant)
        if options['enqueue_only']:
            return
        while True:
            close_old_connections()
            processed = run_preparation_repair()
            if options['once']:
                return
            if not processed:
                time.sleep(max(1, options['interval']))
