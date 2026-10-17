"""Cold deployments prepare immutable schemas before admitting concurrent starts."""
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from .event_services import EventSchemaDriftError
from .models import EventSchemaArtifact


class EventSchemaPreparationTests(TestCase):
    def test_prepares_latest_capture_schemas_once_without_creating_student_events(self):
        from .models import AssessmentEvent
        call_command('prepare_assessment_event_schemas', stdout=StringIO())
        first = dict(EventSchemaArtifact.objects.values_list('pk', 'schema_sha256'))
        self.assertTrue(EventSchemaArtifact.objects.filter(event_type='session_started', event_version=1).exists())
        self.assertTrue(EventSchemaArtifact.objects.filter(event_type='item_rendered', event_version=3).exists())
        self.assertTrue(EventSchemaArtifact.objects.filter(event_type='item_entered', event_version=4).exists())
        self.assertTrue(EventSchemaArtifact.objects.filter(event_type='response_transition', event_version=1).exists())
        call_command('prepare_assessment_event_schemas', stdout=StringIO())
        self.assertEqual(dict(EventSchemaArtifact.objects.values_list('pk', 'schema_sha256')), first)
        self.assertFalse(AssessmentEvent.objects.exists())

    def test_detects_existing_schema_drift_without_replacing_artifacts(self):
        call_command('prepare_assessment_event_schemas', stdout=StringIO())
        before = list(EventSchemaArtifact.objects.order_by('pk').values_list('pk', 'schema_sha256'))
        with patch('apps.assessment.event_services.event_schema_sha256', return_value='0' * 64):
            with self.assertRaises(EventSchemaDriftError):
                call_command('prepare_assessment_event_schemas', stdout=StringIO())
        self.assertEqual(list(EventSchemaArtifact.objects.order_by('pk').values_list('pk', 'schema_sha256')), before)
