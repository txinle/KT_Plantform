"""Persistent receipt processing; a failed frozen paper can be retried by staff."""
import logging
import json
import os
import socket
import time

from django import db
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.assessment.session_submission_models import SessionSubmission
from apps.assessment.session_submission_services import (WORKER_APPLICATION_NAME, claim_session_submission,
    observe_submission_queue, run_session_submission, submission_worker_connection_is_pooled)

logger = logging.getLogger(__name__)


def mark_worker_connection():
    connection = db.connection
    if (connection.vendor == 'postgresql' and not submission_worker_connection_is_pooled(connection)
            and connection.connection is not None):
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('application_name', %s, false)", [WORKER_APPLICATION_NAME])
        # A worker keeps its tagged session between polls; normal error cleanup is retained.
        connection.close_at = None


class Command(BaseCommand):
    help = 'Process durable whole-session submissions with finite retries and lease recovery.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--status', action='store_true', help='Report queue age and DB-connected workers without claiming work.')
        parser.add_argument('--poll-seconds', type=float, default=2)
        parser.add_argument('--retry-failed', type=str, help='Receipt UUID to requeue without changing frozen material.')

    def handle(self, *args, **options):
        if options.get('status'):
            self.stdout.write(json.dumps(observe_submission_queue()))
            return
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
                mark_worker_connection()
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
