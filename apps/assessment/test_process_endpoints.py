import json
import uuid
from datetime import timedelta
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.experiments.models import Experiment, ExperimentVersion, QuestionSnapshot
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.question_bank.models import Question, QuestionBank

from .models import (
    AssessmentEvent,
    Attempt,
    ClientTimeSync,
    ExperimentSession,
    StudentQuestionAssignment,
)


class ProcessEndpointContractTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="process-api-teacher",
            role=User.Role.TEACHER,
        )
        self.student = User.objects.create_user(
            username="PROCESS-API-S1",
            student_number="PROCESS-API-S1",
            role=User.Role.STUDENT,
        )
        self.other_student = User.objects.create_user(
            username="PROCESS-API-S2",
            student_number="PROCESS-API-S2",
            role=User.Role.STUDENT,
        )
        bank = QuestionBank.objects.create(owner=self.teacher, name="过程 API 题库")
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SINGLE,
            text="过程 API 题目",
            correct_answer="A",
        )
        experiment = Experiment.objects.create(
            owner=self.teacher,
            question_bank=bank,
            name="过程 API 实验",
        )
        version = ExperimentVersion.objects.create(
            experiment=experiment,
            version_number=1,
            published_by=self.teacher,
        )
        snapshot = QuestionSnapshot.objects.create(
            version=version,
            source_question=question,
            source_sequence=1,
            code="Q1",
            question_type=Question.Type.SINGLE,
            text="过程 API 题目",
            options=[{"key": "A", "text": "甲"}, {"key": "B", "text": "乙"}],
            correct_answer="A",
        )
        item_instance = get_or_create_default_item_instance(snapshot)
        self.session = ExperimentSession.objects.create(
            version=version,
            student=self.student,
        )
        assignment = StudentQuestionAssignment.objects.create(
            session=self.session,
            snapshot=snapshot,
            item_instance=item_instance,
            assigned_sequence_index=1,
        )
        self.attempt = Attempt.objects.create(
            session=self.session,
            assignment=assignment,
        )
        self.other_session = ExperimentSession.objects.create(
            version=version,
            student=self.other_student,
        )
        other_assignment = StudentQuestionAssignment.objects.create(
            session=self.other_session,
            snapshot=snapshot,
            item_instance=item_instance,
            assigned_sequence_index=1,
        )
        self.other_attempt = Attempt.objects.create(
            session=self.other_session,
            assignment=other_assignment,
        )
        self.client.force_login(self.student)
        self.client_instance_id = uuid.uuid4()
        self.batch_url = reverse("assessment_process:event_batch")
        self.sync_create_url = reverse("assessment_process:time_sync_create")
        self.sync_finalize_url = reverse("assessment_process:time_sync_finalize")

    def post_json(self, url, payload, *, client=None):
        return (client or self.client).post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def event(self, sequence=1, **overrides):
        now = timezone.now()
        value = {
            "event_type": "item_rendered",
            "event_version": 1,
            "session_id": self.session.pk,
            "attempt_id": self.attempt.pk,
            "client_sequence_no": sequence,
            "client_wall_time_utc": now.isoformat(),
            "client_monotonic_ms": "1250.250",
            "client_time_origin": (now - timedelta(seconds=1)).isoformat(),
            "payload": {},
            "quality_flags": [],
            "app_version": "collector-test-v1",
        }
        value.update(overrides)
        return value

    def batch(self, events=None, **overrides):
        value = {
            "client_instance_id": str(self.client_instance_id),
            "events": events if events is not None else [self.event()],
        }
        value.update(overrides)
        return value

    def sync_create_payload(self, **overrides):
        now = timezone.now()
        value = {
            "session_id": self.session.pk,
            "client_instance_id": str(self.client_instance_id),
            "client_wall_time_utc": now.isoformat(),
            "client_monotonic_ms": "1000.000",
            "client_time_origin": (now - timedelta(seconds=1)).isoformat(),
        }
        value.update(overrides)
        return value

    def test_process_endpoints_require_csrf_and_student_role(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.student)
        self.assertEqual(
            self.post_json(
                self.batch_url,
                self.batch(),
                client=csrf_client,
            ).status_code,
            403,
        )

        teacher_client = Client()
        teacher_client.force_login(self.teacher)
        self.assertEqual(
            self.post_json(
                self.batch_url,
                self.batch(),
                client=teacher_client,
            ).status_code,
            403,
        )
        self.assertEqual(AssessmentEvent.objects.count(), 0)

    def test_valid_batch_acknowledges_each_client_sequence_in_order(self):
        response = self.post_json(
            self.batch_url,
            self.batch(events=[self.event(3), self.event(7)]),
        )

        self.assertEqual(response.status_code, 200)
        acknowledgements = response.json()["acknowledgements"]
        self.assertEqual(
            [item["client_sequence_no"] for item in acknowledgements],
            [3, 7],
        )
        self.assertEqual(
            [item["server_ingest_sequence_no"] for item in acknowledgements],
            [1, 2],
        )
        self.assertEqual(AssessmentEvent.objects.count(), 2)

    def test_browser_monotonic_precision_is_normalized_to_storage_contract(self):
        sync_response = self.post_json(
            self.sync_create_url,
            self.sync_create_payload(client_monotonic_ms="1000.1234567"),
        )
        event_response = self.post_json(
            self.batch_url,
            self.batch(
                events=[self.event(client_monotonic_ms="1250.9876543")]
            ),
        )

        self.assertEqual(sync_response.status_code, 201)
        self.assertEqual(event_response.status_code, 200)
        self.assertEqual(
            ClientTimeSync.objects.get().client_monotonic_ms,
            Decimal("1000.123"),
        )
        self.assertEqual(
            AssessmentEvent.objects.get().client_monotonic_ms,
            Decimal("1250.988"),
        )

    def test_batch_resolves_links_from_authenticated_student(self):
        response = self.post_json(
            self.batch_url,
            self.batch(
                events=[
                    self.event(
                        session_id=self.other_session.pk,
                        attempt_id=self.other_attempt.pk,
                    )
                ]
            ),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_event_link")
        self.assertEqual(AssessmentEvent.objects.count(), 0)

    def test_batch_validation_is_atomic_and_does_not_echo_payload_values(self):
        secret = "student-private-answer"
        response = self.post_json(
            self.batch_url,
            self.batch(
                events=[
                    self.event(1),
                    self.event(2, payload={"undeclared": secret}),
                ]
            ),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_event")
        self.assertNotIn(secret, response.content.decode("utf-8"))
        self.assertEqual(AssessmentEvent.objects.count(), 0)

    def test_identical_batch_replays_and_conflicting_client_key_is_409(self):
        payload = self.batch()
        first = self.post_json(self.batch_url, payload)
        replay = self.post_json(self.batch_url, payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(
            replay.json()["acknowledgements"][0]["event_id"],
            first.json()["acknowledgements"][0]["event_id"],
        )
        conflict = self.post_json(
            self.batch_url,
            self.batch(events=[self.event(payload={"unexpected": "secret"})]),
        )
        self.assertEqual(conflict.status_code, 400)

        conflict = self.post_json(
            self.batch_url,
            self.batch(
                events=[
                    self.event(
                        event_type="item_left",
                        payload={"leave_reason": "navigation"},
                    )
                ]
            ),
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "event_conflict")
        self.assertEqual(AssessmentEvent.objects.count(), 1)

    def test_conflict_rolls_back_new_events_earlier_in_the_same_batch(self):
        existing = self.post_json(
            self.batch_url,
            self.batch(events=[self.event(7)]),
        )
        self.assertEqual(existing.status_code, 200)

        conflict = self.post_json(
            self.batch_url,
            self.batch(
                events=[
                    self.event(
                        3,
                        event_type="item_left",
                        payload={"leave_reason": "navigation"},
                    ),
                    self.event(
                        7,
                        event_type="item_left",
                        payload={"leave_reason": "pagehide"},
                    ),
                ]
            ),
        )

        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(AssessmentEvent.objects.count(), 1)
        self.assertFalse(
            AssessmentEvent.objects.filter(
                client_instance_id=self.client_instance_id,
                client_sequence_no=3,
            ).exists()
        )

    def test_batch_limits_and_malformed_envelopes_are_rejected(self):
        cases = (
            (self.batch(events=[self.event(i + 1) for i in range(101)]), 400),
            (self.batch(client_instance_id="not-a-uuid"), 400),
            (self.batch(events=[self.event(client_monotonic_ms="NaN")]), 400),
            (self.batch(events=[self.event(client_wall_time_utc="not-time")]), 400),
            (
                self.batch(events=[self.event(quality_flags=["invented_flag"])]),
                400,
            ),
            (self.batch(events=[self.event(event_version=99)]), 400),
            (self.batch(events=[self.event(2), self.event(1)]), 400),
            (
                self.batch(
                    events=[
                        self.event(
                            event_type="response_committed",
                            payload={
                                "submission_token": str(uuid.uuid4()),
                                "score": 1,
                                "is_correct": True,
                            },
                        )
                    ]
                ),
                400,
            ),
        )
        for payload, expected_status in cases:
            with self.subTest(payload=payload):
                response = self.post_json(self.batch_url, payload)
                self.assertEqual(response.status_code, expected_status)
        self.assertEqual(AssessmentEvent.objects.count(), 0)

    def test_time_sync_endpoints_reject_malformed_measurements(self):
        invalid_create_documents = (
            self.sync_create_payload(client_instance_id="not-a-uuid"),
            self.sync_create_payload(client_wall_time_utc="not-time"),
            self.sync_create_payload(client_monotonic_ms="NaN"),
            self.sync_create_payload(
                client_time_origin="2026-08-26T04:00:00"
            ),
        )
        for document in invalid_create_documents:
            with self.subTest(document=document):
                response = self.post_json(self.sync_create_url, document)
                self.assertEqual(response.status_code, 400)
        self.assertEqual(ClientTimeSync.objects.count(), 0)

    def test_batch_rejects_oversized_body_before_json_processing(self):
        response = self.post_json(
            self.batch_url,
            self.batch(extra_padding="x" * (300 * 1024)),
        )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"]["code"], "request_too_large")

    def test_time_sync_create_and_finalize_are_student_owned_and_idempotent(self):
        create_response = self.post_json(
            self.sync_create_url,
            self.sync_create_payload(),
        )
        self.assertEqual(create_response.status_code, 201)
        create_data = create_response.json()
        sample = ClientTimeSync.objects.get(pk=create_data["time_sync_id"])
        self.assertEqual(sample.session_id, self.session.pk)
        self.assertEqual(sample.sync_quality, ClientTimeSync.SyncQuality.PENDING)

        client_receive_wall = timezone.now()
        finalize_payload = {
            "time_sync_id": str(sample.pk),
            "client_instance_id": str(self.client_instance_id),
            "client_received_wall_time_utc": client_receive_wall.isoformat(),
            "client_received_monotonic_ms": "1030.000",
        }
        finalized = self.post_json(self.sync_finalize_url, finalize_payload)
        replay = self.post_json(self.sync_finalize_url, finalize_payload)
        self.assertEqual(finalized.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(
            replay.json()["sync_quality"],
            finalized.json()["sync_quality"],
        )

        conflicting = self.post_json(
            self.sync_finalize_url,
            {**finalize_payload, "client_received_monotonic_ms": "1031.000"},
        )
        self.assertEqual(conflicting.status_code, 409)

    def test_time_sync_endpoints_reject_foreign_session_and_foreign_sample(self):
        foreign_create = self.post_json(
            self.sync_create_url,
            self.sync_create_payload(session_id=self.other_session.pk),
        )
        self.assertEqual(foreign_create.status_code, 400)

        self.client.force_login(self.other_student)
        created = self.post_json(
            self.sync_create_url,
            self.sync_create_payload(session_id=self.other_session.pk),
        )
        self.assertEqual(created.status_code, 201)
        self.client.force_login(self.student)
        foreign_finalize = self.post_json(
            self.sync_finalize_url,
            {
                "time_sync_id": created.json()["time_sync_id"],
                "client_instance_id": str(self.client_instance_id),
                "client_received_wall_time_utc": timezone.now().isoformat(),
                "client_received_monotonic_ms": "1030.000",
            },
        )
        self.assertEqual(foreign_finalize.status_code, 400)

    def test_process_endpoint_requires_json_and_rejects_unknown_top_level_fields(self):
        form_response = self.client.post(self.batch_url, data={"events": "[]"})
        self.assertEqual(form_response.status_code, 415)
        unknown_field = self.post_json(
            self.batch_url,
            self.batch(undeclared="value"),
        )
        self.assertEqual(unknown_field.status_code, 400)
