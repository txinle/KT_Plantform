"""Reconcile a bounded page of global admission executions."""
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from apps.assessment.admission import AdmissionCoordinator, AdmissionUnavailable
from apps.assessment.admission_reconciliation import reconcile_admission_page


class Command(BaseCommand):
    help = 'Reconcile bounded executing admissions using committed receipts or durable cancellation fences.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=64)
        parser.add_argument('--cursor', type=int, default=0)

    def handle(self, *args, **options):
        if not AdmissionCoordinator.global_enabled():
            raise CommandError('Experimental global admission is disabled')
        if not 1 <= options['limit'] <= 256 or options['cursor'] < 0:
            raise CommandError('Use limit 1..256 and a nonnegative cursor')
        try:
            result = reconcile_admission_page(cursor=options['cursor'], limit=options['limit'])
        except (AdmissionUnavailable, DatabaseError) as error:
            raise CommandError('Admission reconciliation unavailable; unresolved executions remain occupied') from error
        self.stdout.write(json.dumps(result, sort_keys=True))
