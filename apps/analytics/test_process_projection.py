from dataclasses import asdict
from datetime import timedelta
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone

from .teaching.process import ProcessCompleteness, project_attempt_process


class AttemptProcessProjectionTests(SimpleTestCase):
    def setUp(self):
        self.generated_at = timezone.now()

    def _event(self, sequence, payload, *, attempt_id=7):
        return SimpleNamespace(
            attempt_id=attempt_id,
            event_type="response_state_changed",
            payload=payload,
            server_ingest_sequence_no=sequence,
            estimated_occurred_at_utc=None,
            server_received_at=self.generated_at + timedelta(seconds=sequence),
        )

    def _snapshot(self, pk, value, state_hash, *, attempt_id=7):
        return SimpleNamespace(
            pk=pk,
            attempt_id=attempt_id,
            state_hash=state_hash,
            content_hash=f"content-{state_hash}",
            content_length=len(value),
            state_json={"question_type": "numeric", "value": value},
        )

    def test_single_choice_projects_value_chain_and_choice_revision_count(self):
        events = [
            self._event(
                1,
                {
                    "question_type": "single_choice",
                    "response_state_before": None,
                    "response_state_after": "A",
                    "change_source": "student",
                },
            ),
            self._event(
                2,
                {
                    "question_type": "single_choice",
                    "response_state_before": "A",
                    "response_state_after": "B",
                    "change_source": "student",
                },
            ),
            self._event(
                3,
                {
                    "question_type": "single_choice",
                    "response_state_before": "B",
                    "response_state_after": "C",
                    "change_source": "student",
                },
            ),
        ]

        projection = project_attempt_process(
            generated_at=self.generated_at,
            attempt_id=7,
            question_type="single_choice",
            events=events,
            snapshots=(),
            observations=(),
            final_answer="C",
            final_snapshot_id=None,
            submitted_at=self.generated_at + timedelta(seconds=4),
        )

        self.assertEqual(projection.completeness, ProcessCompleteness.COMPLETE)
        self.assertEqual(
            [step.value for step in projection.steps],
            ["A", "B", "C", "C"],
        )
        self.assertEqual(projection.revision_count, 2)
        self.assertEqual(projection.first_value, "A")
        self.assertEqual(projection.final_value, "C")

    def test_broken_choice_chain_is_incomplete_and_does_not_guess(self):
        events = [
            self._event(
                1,
                {
                    "question_type": "single_choice",
                    "response_state_before": None,
                    "response_state_after": "A",
                },
            ),
            self._event(
                2,
                {
                    "question_type": "single_choice",
                    "response_state_before": "B",
                    "response_state_after": "C",
                },
            ),
        ]

        projection = project_attempt_process(
            generated_at=self.generated_at,
            attempt_id=7,
            question_type="single_choice",
            events=events,
            snapshots=(),
            observations=(),
            final_answer="C",
            final_snapshot_id=None,
            submitted_at=self.generated_at,
        )

        self.assertEqual(projection.completeness, ProcessCompleteness.INCOMPLETE)
        self.assertEqual(projection.steps, ())
        self.assertIsNone(projection.revision_count)
        self.assertIsNone(projection.first_value)
        self.assertNotIn("犹豫", str(asdict(projection)))

    def test_numeric_projects_verified_snapshot_values_without_revision_metric(self):
        snapshots = (
            self._snapshot("s1", "12", "h1"),
            self._snapshot("s2", "120", "h2"),
            self._snapshot("s3", "12.5", "h3"),
        )
        events = []
        before = None
        for sequence, snapshot in enumerate(snapshots, start=1):
            after = {
                "snapshot_id": snapshot.pk,
                "state_hash": snapshot.state_hash,
                "content_length": snapshot.content_length,
            }
            events.append(
                self._event(
                    sequence,
                    {
                        "question_type": "numeric",
                        "response_state_before": before,
                        "response_state_after": after,
                    },
                )
            )
            before = after

        projection = project_attempt_process(
            generated_at=self.generated_at,
            attempt_id=7,
            question_type="numeric",
            events=events,
            snapshots=snapshots,
            observations=(),
            final_answer="12.5",
            final_snapshot_id="s3",
            submitted_at=self.generated_at + timedelta(seconds=5),
        )

        self.assertEqual(projection.completeness, ProcessCompleteness.COMPLETE)
        self.assertEqual(
            [step.value for step in projection.steps],
            ["12", "120", "12.5", "12.5"],
        )
        self.assertIsNone(projection.revision_count)

    def test_long_text_returns_operations_lengths_and_final_answer_only(self):
        observations = (
            SimpleNamespace(
                attempt_id=7,
                edit_operation="insert",
                content_hash_before="h0",
                content_hash_after="h1",
                content_length_before=0,
                content_length_after=18,
                observed_at=self.generated_at,
                private_text="不应泄露的中间正文一",
            ),
            SimpleNamespace(
                attempt_id=7,
                edit_operation="delete",
                content_hash_before="h1",
                content_hash_after="h2",
                content_length_before=18,
                content_length_after=12,
                observed_at=self.generated_at + timedelta(seconds=1),
                private_text="不应泄露的中间正文二",
            ),
        )
        final_snapshot = SimpleNamespace(
            pk="final",
            attempt_id=7,
            state_hash="final-state",
            content_hash="h2",
            content_length=12,
            state_json={"question_type": "short_answer", "value": "最终答案正文"},
        )

        projection = project_attempt_process(
            generated_at=self.generated_at,
            attempt_id=7,
            question_type="short_answer",
            events=(),
            snapshots=(final_snapshot,),
            observations=observations,
            final_answer="最终答案正文",
            final_snapshot_id="final",
            submitted_at=self.generated_at + timedelta(seconds=2),
        )
        serialized = str(asdict(projection))

        self.assertEqual(projection.completeness, ProcessCompleteness.COMPLETE)
        self.assertEqual(
            [(step.kind, step.length_before, step.length_after) for step in projection.steps],
            [("insert", 0, 18), ("delete", 18, 12), ("submit", None, 12)],
        )
        self.assertEqual(projection.final_value, "最终答案正文")
        self.assertNotIn("不应泄露的中间正文", serialized)
        for forbidden in ("payload", "hash", "client", "snapshot_id", "event_id"):
            self.assertNotIn(forbidden, serialized)

    def test_cross_attempt_or_hash_mismatch_marks_text_chain_incomplete(self):
        snapshot = self._snapshot("s1", "12", "actual", attempt_id=99)
        event = self._event(
            1,
            {
                "question_type": "numeric",
                "response_state_before": None,
                "response_state_after": {
                    "snapshot_id": "s1",
                    "state_hash": "claimed",
                    "content_length": 2,
                },
            },
        )

        projection = project_attempt_process(
            generated_at=self.generated_at,
            attempt_id=7,
            question_type="numeric",
            events=(event,),
            snapshots=(snapshot,),
            observations=(),
            final_answer="12",
            final_snapshot_id="s1",
            submitted_at=self.generated_at,
        )

        self.assertEqual(projection.completeness, ProcessCompleteness.INCOMPLETE)
        self.assertEqual(projection.steps, ())
