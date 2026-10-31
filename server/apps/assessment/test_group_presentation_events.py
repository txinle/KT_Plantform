from django.test import SimpleTestCase
from .event_batch_services import CLIENT_BATCH_EVENT_TYPES
from .event_schema_registry import REGISTERED_EVENT_TYPES


class GroupPresentationSchemaTests(SimpleTestCase):
    def test_group_display_does_not_extend_the_existing_collection_contract(self):
        for event_type in ('group_presented', 'group_left'):
            self.assertNotIn(event_type, CLIENT_BATCH_EVENT_TYPES)
            self.assertNotIn(event_type, REGISTERED_EVENT_TYPES)
