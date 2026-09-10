import importlib
import uuid
from importlib.metadata import PackageNotFoundError, version

from django.test import SimpleTestCase


EXPECTED_EVENT_TYPES = (
    "session_started",
    "session_completed",
    "session_expired",
    "session_invalidated",
    "item_entered",
    "item_rendered",
    "item_left",
    "response_input_started",
    "text_edit_observed",
    "response_state_changed",
    "response_commit_requested",
    "response_committed",
    "response_commit_rejected",
    "attempt_closed",
    "feedback_shown",
    "explanation_shown",
    "hint_opened",
    "hint_closed",
    "resource_opened",
    "resource_closed",
    "document_visible",
    "document_hidden",
    "window_focused",
    "window_blurred",
    "pageshow",
    "pagehide",
    "network_online",
    "network_offline",
    "activity_heartbeat",
    "confidence_prompt_shown",
    "confidence_committed",
    "confidence_skipped",
    "simulation_parameter_changed",
    "simulation_run_started",
    "simulation_run_completed",
    "simulation_error_returned",
    "legacy_visit_observed",
    "legacy_draft_observed",
    "legacy_submission_observed",
    "legacy_session_start_observed",
    "legacy_session_end_observed",
)


def load_registry_module():
    try:
        return importlib.import_module("apps.assessment.event_schema_registry")
    except ModuleNotFoundError:
        return None


class JsonSchemaDependencyTests(SimpleTestCase):
    def test_supported_jsonschema_version_is_installed(self):
        try:
            installed_version = version("jsonschema")
        except PackageNotFoundError:
            installed_version = None

        self.assertEqual(installed_version, "4.26.0")


class EventSchemaRegistryContractTests(SimpleTestCase):
    def test_registry_module_and_public_api_exist(self):
        registry = load_registry_module()

        self.assertIsNotNone(registry)
        for name in (
            "EventSchemaNotFound",
            "EventPayloadValidationError",
            "get_event_schema",
            "validate_event_payload",
            "event_schema_sha256",
            "registered_event_schemas",
        ):
            self.assertTrue(hasattr(registry, name), name)

    def test_every_approved_event_has_an_independent_v1_schema(self):
        registry = load_registry_module()
        self.assertIsNotNone(registry)

        registered = registry.registered_event_schemas()
        self.assertEqual(registered, tuple((event_type, 1) for event_type in EXPECTED_EVENT_TYPES))
        for event_type in EXPECTED_EVENT_TYPES:
            schema = registry.get_event_schema(event_type, 1)
            self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
            self.assertEqual(schema["type"], "object")
            self.assertFalse(schema["additionalProperties"])

    def test_response_state_payload_accepts_the_documented_shape(self):
        registry = load_registry_module()
        self.assertIsNotNone(registry)

        registry.validate_event_payload(
            "response_state_changed",
            1,
            {
                "question_type": "single_choice",
                "response_state_after": "A",
                "change_source": "student",
            },
        )

    def test_capture_v2_feedback_exposure_has_an_explicit_content_contract(self):
        registry = load_registry_module()

        registry.validate_event_payload(
            "feedback_exposure",
            1,
            {
                "capture_version": "capture-v2",
                "operation_id": str(uuid.uuid4()),
                "feedback_mode": "full",
                "stage": "explanation_presented",
                "answer_event_id": str(uuid.uuid4()),
                "snapshot_id": str(uuid.uuid4()),
                "included_correctness": True,
                "included_correct_answer": True,
                "included_explanation": True,
            },
        )

    def test_response_state_payload_accepts_snapshot_references_for_text_like_items(self):
        registry = load_registry_module()
        self.assertIsNotNone(registry)

        registry.validate_event_payload(
            "response_state_changed",
            1,
            {
                "question_type": "short_answer",
                "response_snapshot_id": str(uuid.uuid4()),
                "content_hash": "a" * 64,
                "content_length": 12,
                "change_source": "student",
            },
        )

    def test_text_edit_payload_carries_only_hashes_and_lengths(self):
        registry = load_registry_module()
        registry.validate_event_payload(
            "text_edit_observed",
            1,
            {
                "question_type": "short_answer",
                "edit_operation": "paste",
                "content_hash_before": "a" * 64,
                "content_hash_after": "b" * 64,
                "content_length_before": 2,
                "content_length_after": 9,
                "input_method": "paste",
            },
        )

    def test_response_state_payload_rejects_raw_text_and_key_level_data(self):
        registry = load_registry_module()
        self.assertIsNotNone(registry)

        for forbidden_payload in (
            {
                "question_type": "short_answer",
                "response_snapshot_id": str(uuid.uuid4()),
                "content_hash": "a" * 64,
                "content_length": 12,
                "change_source": "student",
                "raw_text": "private answer",
            },
            {
                "question_type": "single_choice",
                "response_state_after": "A",
                "change_source": "student",
                "key_pressed": "A",
            },
        ):
            with self.subTest(payload_keys=tuple(forbidden_payload)):
                with self.assertRaises(registry.EventPayloadValidationError):
                    registry.validate_event_payload(
                        "response_state_changed",
                        1,
                        forbidden_payload,
                    )

    def test_response_state_payload_requires_the_shape_for_its_question_type(self):
        registry = load_registry_module()
        self.assertIsNotNone(registry)

        invalid_payloads = (
            {
                "question_type": "short_answer",
                "response_state_after": "raw answer",
                "change_source": "student",
            },
            {
                "question_type": "single_choice",
                "response_snapshot_id": str(uuid.uuid4()),
                "content_hash": "a" * 64,
                "content_length": 1,
                "change_source": "student",
            },
        )
        for payload in invalid_payloads:
            with self.subTest(question_type=payload["question_type"]):
                with self.assertRaises(registry.EventPayloadValidationError):
                    registry.validate_event_payload(
                        "response_state_changed",
                        1,
                        payload,
                    )

    def test_heartbeat_rejects_undeclared_tracking_fields(self):
        registry = load_registry_module()
        self.assertIsNotNone(registry)

        with self.assertRaises(registry.EventPayloadValidationError) as raised:
            registry.validate_event_payload(
                "activity_heartbeat",
                1,
                {
                    "attempt_id": 4,
                    "document_visible": True,
                    "window_focused": True,
                    "last_device_interaction_age_ms": 200,
                    "mouse_x": 10,
                },
            )

        self.assertIn("activity_heartbeat:v1", str(raised.exception))
        self.assertIn(" at $", str(raised.exception))
        self.assertNotIn("mouse_x", str(raised.exception))

    def test_uuid_formats_are_enforced(self):
        registry = load_registry_module()
        self.assertIsNotNone(registry)

        with self.assertRaises(registry.EventPayloadValidationError):
            registry.validate_event_payload(
                "response_commit_requested",
                1,
                {"submission_token": "not-a-uuid"},
            )

    def test_unknown_event_version_has_a_typed_error(self):
        registry = load_registry_module()
        self.assertIsNotNone(registry)

        with self.assertRaises(registry.EventSchemaNotFound):
            registry.get_event_schema("session_started", 3)

    def test_confidence_v2_accepts_one_percent_values_without_weakening_v1(self):
        registry = load_registry_module()
        assignment_id = str(uuid.uuid4())

        with self.assertRaises(registry.EventPayloadValidationError):
            registry.validate_event_payload(
                "confidence_committed",
                1,
                {"confidence_assignment_id": assignment_id, "probability": 57},
            )
        registry.validate_event_payload(
            "confidence_committed",
            2,
            {"confidence_assignment_id": assignment_id, "probability": 57},
        )

    def test_schema_hash_is_stable_sha256(self):
        registry = load_registry_module()
        self.assertIsNotNone(registry)

        first = registry.event_schema_sha256("response_state_changed", 1)
        second = registry.event_schema_sha256("response_state_changed", 1)
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        self.assertEqual(first, second)
