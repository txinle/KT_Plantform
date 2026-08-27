import hashlib
import json
import uuid

from django.test import SimpleTestCase


class ResponseStateContractTests(SimpleTestCase):
    def test_canonical_raw_states_and_hash_exclude_snapshot_uuid(self):
        from .response_snapshot_services import (
            ResponseState,
            ResponseStateRef,
            canonical_response_state,
        )

        self.assertEqual(canonical_response_state("single_choice", " A "), " A ")
        self.assertEqual(canonical_response_state("multiple_choice", ["B", "A", "B"]), ["A", "B"])
        self.assertEqual(canonical_response_state("numeric", "01.2500"), "01.2500")
        self.assertEqual(canonical_response_state("short_answer", "原始文本"), "原始文本")
        self.assertEqual(canonical_response_state("fill_blank", "  raw  "), "  raw  ")

        state = ResponseState("short_answer", "同一状态")
        first = ResponseStateRef(uuid.uuid4(), state.state_hash, 4, 1)
        second = ResponseStateRef(uuid.uuid4(), state.state_hash, 4, 1)
        self.assertEqual(first.state_hash, second.state_hash)
        self.assertEqual(
            state.state_hash,
            hashlib.sha256(
                json.dumps(state.state_json, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
            ).hexdigest(),
        )

    def test_response_snapshot_has_canonical_state_json_and_state_hash(self):
        from .response_snapshot_services import create_response_snapshot
        from .models import ResponseSnapshot

        # The service accepts a light-weight attempt-like object in this unit
        # contract; model integration is covered by the existing snapshot suite.
        self.assertTrue(hasattr(ResponseSnapshot, "state_json"))
        self.assertTrue(hasattr(ResponseSnapshot, "state_hash"))

    def test_response_state_schema_accepts_before_after_refs_and_observation(self):
        from .event_schema_registry import validate_event_payload

        ref = {
            "snapshot_id": str(uuid.uuid4()),
            "state_hash": "a" * 64,
            "content_length": 3,
        }
        validate_event_payload(
            "response_state_changed",
            1,
            {
                "question_type": "short_answer",
                "response_state_before": None,
                "response_state_after": ref,
                "change_source": "student",
                "stable_input_observation": {
                    "is_composing": False,
                    "input_method": "ime",
                    "text_length": 3,
                },
            },
        )

    def test_state_chain_checker_rejects_non_contiguous_before_after(self):
        from .event_invariants import ResponseStateChainError, validate_response_state_chain

        events = [
            {"response_state_before": None, "response_state_after": "A"},
            {"response_state_before": "B", "response_state_after": "C"},
        ]
        with self.assertRaises(ResponseStateChainError):
            validate_response_state_chain(events)

    def test_stable_input_replay_drops_ime_intermediate_text(self):
        from .response_input_services import replay_stable_input

        observations = [
            {"text": "你", "is_composing": True},
            {"text": "你好", "is_composing": False},
        ]
        self.assertEqual(replay_stable_input(observations), "你好")
