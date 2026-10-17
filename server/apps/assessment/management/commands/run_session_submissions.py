"""Persistent receipt processing; a failed frozen paper can be retried by staff."""
import logging
import os
import socket
import time

from django import db
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.assessment.session_submission_models import SessionSubmission
from apps.assessment.session_submission_services import claim_session_submission, run_session_submission

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Process durable whole-session submissions with finite retries and lease recovery.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--poll-seconds', type=float, default=2)
        parser.add_argument('--retry-failed', type=str, help='Receipt UUID to requeue without changing frozen material.')

    def handle(self, *args, **options):
        if options['retry_failed']:
            count = SessionSubmission.objects.filter(pk=options['retry_failed'], status='failed').update(
                status='queued', attempts=0, next_attempt_at=timezone.now(), error_code='',
                lease_token=None, lease_expires_at=None)
            self.stdout.write(f'Requeued {count} receipt(s).')
        worker_id = f'{socket.gethostname()}:{os.getpid()}'
        while True:
            job = None
            db.close_old_connections()
            try:
                job = claim_session_submission(worker_id=worker_id)
                if job is not None:
                    result = run_session_submission(job)
                    self.stdout.write(f'{result.pk}: {result.status}')
            except (db.OperationalError, db.InterfaceError) as error:
                logger.warning('Session submission database unavailable; lease recovery retained error_type=%s',
                               type(error).__name__)
                db.connections.close_all()
                if options['once']:
                    raise
                job = None
            finally:
                db.close_old_connections()
            if options['once']:
                return
            if job is None:
                time.sleep(max(0.1, options['poll_seconds']))
