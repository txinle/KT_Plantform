"""Prepare immutable registry artifacts before admitting concurrent assessment writes."""
import json

from django.core.management.base import BaseCommand

from apps.assessment.event_schema_registry import REGISTERED_EVENT_TYPES, SCHEMA_ROOT
from apps.assessment.event_services import _register_schema_artifact


class Command(BaseCommand):
    help = 'Validate and prepare all installed assessment event schema versions without student events.'

    def handle(self, *args, **options):
        prepared = 0
        for event_type in REGISTERED_EVENT_TYPES:
            for path in sorted((SCHEMA_ROOT / event_type).glob('v[0-9]*.json')):
                _register_schema_artifact(event_type, int(path.stem[1:]))
                prepared += 1
        self.stdout.write(json.dumps({'validated_schema_versions': prepared}))
