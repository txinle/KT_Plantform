import uuid
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.experiments.models import Condition, ConditionAssignment

from .event_services import append_assessment_event
from .models import AssessmentEvent, ResponseInputObservation
from .services import save_draft, submit_attempt
from .writer_lease_services import WriterLeaseConflict, acquire_attempt_writer_lease


class RebuildContractTests(TestCase):
    def setUp(self):
        from .test_submission_grade_contract import SubmissionGradeContractTests

        # Reuse the small published experiment fixture without copying its
        # construction logic into the contract tests.
        fixture = SubmissionGradeContractTests()
        fixture.setUp()
        self.attempt = fixture.attempt
        self.session = self.attempt.session

    def test_response_committed_payload_is_submission_fact_not_grade(self):
        save_draft(self.attempt.pk, "a")
        submitted = submit_attempt(self.attempt.pk, uuid.uuid4())
        event = AssessmentEvent.objects.get(attempt=submitted, event_type="response_committed")
        submission = submitted.submission_event
        self.assertEqual(event.payload["submission_id"], submission.pk)
        self.assertEqual(
            event.payload["final_response_snapshot_id"],
            str(submission.final_response_snapshot_id),
        )
        self.assertEqual(event.payload["grading_status"], submission.grading_status)
        self.assertNotIn("score", event.payload)
        self.assertNotIn("is_correct", event.payload)

    def test_monotonic_time_cannot_move_back_within_client_instance(self):
        client = uuid.uuid4()
        now = timezone.now()
        kwargs = dict(
            event_type="item_rendered",
            event_version=1,
            event_domain="assessment",
            actor_source="client",
            session=self.session,
            assignment=self.attempt.assignment,
            attempt=self.attempt,
            question_snapshot=self.attempt.assignment.snapshot,
            item_instance=self.attempt.assignment.item_instance,
            client_instance_id=client,
            client_time_origin=now,
            payload={},
        )
        append_assessment_event(client_sequence_no=1, client_monotonic_ms=Decimal("10"), **kwargs)
        with self.assertRaises(ValueError):
            append_assessment_event(client_sequence_no=2, client_monotonic_ms=Decimal("9"), **kwargs)

    def test_active_writer_lease_blocks_unqualified_mutation(self):
        lease = acquire_attempt_writer_lease(self.attempt, client_instance_id=uuid.uuid4())
        with self.assertRaises(WriterLeaseConflict):
            save_draft(self.attempt.pk, "blocked")
        save_draft(
            self.attempt.pk,
            "allowed",
            writer_lease_token=lease.token,
            writer_client_instance_id=lease.client_instance_id,
        )

    def test_condition_assignment_accepts_canonical_evidence_aliases(self):
        condition = Condition.objects.create(experiment=self.session.version.experiment, key="control")
        assignment = ConditionAssignment(
            condition=condition,
            session=self.session,
            algorithm="random",
            algorithm_version="v1",
            randomization_seed="seed",
            randomization_draw=Decimal("0.2"),
            assignment_probability=Decimal("0.5"),
        )
        assignment.save()
        self.assertEqual(assignment.randomization_seed, "seed")
        self.assertEqual(assignment.randomization_draw, Decimal("0.2"))
        self.assertEqual(assignment.assignment_probability, Decimal("0.5"))


class ResponseInputObservationTests(TestCase):
    def test_observation_contains_only_hashes_and_lengths(self):
        self.assertTrue(hasattr(ResponseInputObservation, "edit_operation"))
        self.assertFalse(hasattr(ResponseInputObservation, "content"))
