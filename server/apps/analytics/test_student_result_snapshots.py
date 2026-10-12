from apps.api.test_support import domain_payload
from unittest.mock import patch
import json

from django.db import DatabaseError
from django.test import TransactionTestCase, override_settings
from rest_framework.renderers import JSONRenderer

from apps.assessment.models import GradeRecord, SubmissionEvent
from apps.assessment.services import grade_submission, void_grade_record
from apps.assessment.test_student_api import StudentApiFixtureMixin
from apps.analytics.models import StudentExperimentResultSnapshot


@override_settings(
    STUDENT_SHARED_RESULTS_ENABLED=True,
    STUDENT_SHARED_RESULTS_COURSE_IDS=frozenset({"*"}),
    CLASS_CENTER_ENABLED=True,
    STUDENT_LEARNING_ANALYTICS_ENABLED=True,
)
class StudentResultSnapshotTests(StudentApiFixtureMixin, TransactionTestCase):
    def test_snapshot_is_scoped_to_student_and_rechecks_feedback_permission(self):
        session = self._session_from(self.start())
        self.complete_session(session)
        first = self.client.get(self._detail_url())
        peer_session = self._session_from(self.start(student=self.peer))
        self.complete_session(peer_session, answer="A", is_correct=False)
        peer = self.client.get(self._detail_url(), HTTP_IF_NONE_MATCH=first["ETag"])
        self.assertEqual(peer.status_code, 200)
        self.assertNotEqual(domain_payload(first)["item_results"], domain_payload(peer)["item_results"])
        self.client.force_login(self.student)
        session.status = session.Status.IN_PROGRESS
        session.terminal_at = None
        session.save(update_fields=["status", "terminal_at"])
        denied = self.client.get(self._detail_url(), HTTP_IF_NONE_MATCH=first["ETag"])
        self.assertEqual(denied.status_code, 403)

    def test_cache_write_failure_returns_live_result(self):
        session = self._session_from(self.start())
        self.complete_session(session)
        with patch("apps.analytics.student_result_snapshots.StudentExperimentResultSnapshot.objects.update_or_create", side_effect=DatabaseError("write unavailable")):
            response = self.client.get(self._detail_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(domain_payload(response)["item_results"]), 1)

    def test_class_membership_changes_invalidate_comparisons(self):
        from apps.accounts.models import CourseEnrollment
        session = self._session_from(self.start())
        self.complete_session(session)
        self.client.get(self._detail_url())
        first = StudentExperimentResultSnapshot.objects.get(class_task=self.task, student=self.student)
        CourseEnrollment.objects.filter(course=self.course, student=self.peer).update(is_active=False)
        self.client.get(self._detail_url())
        second = StudentExperimentResultSnapshot.objects.get(class_task=self.task, student=self.student)
        self.assertEqual(first.source_signature, second.source_signature)

    def test_snapshot_preserves_live_api_value_types(self):
        from apps.accounts.models import CourseEnrollment
        from apps.assessment.api.serializers import StudentLearningExperimentSerializer
        from apps.analytics.teaching.student_projection import (
            build_student_experiment_projection, student_projection_to_api,
        )
        session = self._session_from(self.start())
        self.complete_session(session)
        enrollment = CourseEnrollment.objects.get(student=self.student, course=self.course)
        projection = build_student_experiment_projection(
            enrollment, experiment_id=self.experiment.pk, require_results=True,
        )
        expected = json.loads(JSONRenderer().render(StudentLearningExperimentSerializer(
            instance=student_projection_to_api(projection),
        ).data))
        with patch("apps.analytics.teaching.student_projection._build_student_experiment_projection", return_value=projection):
            actual = self.client.get(self._detail_url())
        actual_payload = domain_payload(actual)
        self.assertIn('version', actual_payload)
        actual_payload.pop('version')
        self.assertEqual(actual_payload, expected)

    def test_peer_raw_revision_does_not_invalidate_personal_snapshot(self):
        session = self._session_from(self.start())
        self.complete_session(session)
        session.research_source_revision = 100
        session.save(update_fields=["research_source_revision"])
        peer_session = self._session_from(self.start(student=self.peer))
        self.complete_session(peer_session)
        self.client.force_login(self.student)
        self.client.get(self._detail_url())
        first = StudentExperimentResultSnapshot.objects.get(class_task=self.task, student=self.student)
        peer_session.research_source_revision += 1
        peer_session.save(update_fields=["research_source_revision"])
        self.client.get(self._detail_url())
        second = StudentExperimentResultSnapshot.objects.get(class_task=self.task, student=self.student)
        self.assertEqual(first.source_signature, second.source_signature)

    def test_cache_read_failure_falls_back_to_live_projection(self):
        session = self._session_from(self.start())
        self.complete_session(session)
        with patch("apps.analytics.student_result_snapshots.StudentExperimentResultSnapshot.objects.filter", side_effect=DatabaseError("cache unavailable")):
            response = self.client.get(self._detail_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(domain_payload(response)["item_results"]), 1)

    def _detail_url(self):
        return (
            f"/api/v1/student/courses/{self.course.pk}/learning/"
            f"experiments/{self.experiment.pk}/"
        )

    def test_result_snapshot_reuses_payload_and_supports_etag(self):
        started = self.start()
        session = self._session_from(started)
        self.complete_session(session)

        first = self.client.get(self._detail_url())
        self.assertEqual(first.status_code, 200, first.content)
        self.assertIn("ETag", first)
        self.assertEqual(first["Cache-Control"], "private, no-cache")
        self.assertEqual(
            StudentExperimentResultSnapshot.objects.filter(
                class_task=self.task,
                student=self.student,
            ).count(),
            1,
        )

        with patch(
            "apps.analytics.teaching.student_projection._build_student_experiment_projection",
            side_effect=AssertionError("snapshot miss unexpectedly rebuilt projection"),
        ):
            cached = self.client.get(self._detail_url())
        self.assertEqual(cached.status_code, 200, cached.content)
        self.assertEqual(domain_payload(cached), domain_payload(first))

        not_modified = self.client.get(
            self._detail_url(),
            HTTP_IF_NONE_MATCH=first["ETag"],
        )
        self.assertEqual(not_modified.status_code, 304)
        self.assertEqual(not_modified["ETag"], first["ETag"])

    def test_grade_and_policy_changes_refresh_snapshot(self):
        started = self.start()
        session = self._session_from(started)
        self.complete_session(session)

        first = self.client.get(self._detail_url())
        self.assertEqual(first.status_code, 200, first.content)
        first_snapshot = StudentExperimentResultSnapshot.objects.get(
            class_task=self.task,
            student=self.student,
        )
        submission = SubmissionEvent.objects.get(
            attempt__session=session,
        )
        grade_submission(
            submission.pk,
            score=0,
            is_correct=False,
            status=GradeRecord.Status.COMPLETED,
        )

        after_grade = self.client.get(self._detail_url())
        self.assertEqual(after_grade.status_code, 200, after_grade.content)
        second_snapshot = StudentExperimentResultSnapshot.objects.get(
            class_task=self.task,
            student=self.student,
        )
        self.assertNotEqual(
            first_snapshot.source_signature,
            second_snapshot.source_signature,
        )
        self.assertEqual(domain_payload(after_grade)["item_results"][0]["score"]["value"], "0.00")

        grade = GradeRecord.objects.get(submission=submission, validity_status=GradeRecord.ValidityStatus.EFFECTIVE)
        void_grade_record(grade.pk, reason="test correction", actor=self.teacher)
        after_void = self.client.get(self._detail_url(), HTTP_IF_NONE_MATCH=after_grade["ETag"])
        self.assertEqual(after_void.status_code, 200)
        self.assertEqual(domain_payload(after_void)["item_results"][0]["result_state"], "pending")

        policy = self.class_group.student_analytics_policy
        policy.show_scores = False
        policy.save(update_fields=["show_scores", "updated_at"])
        after_policy = self.client.get(self._detail_url())
        self.assertEqual(after_policy.status_code, 200, after_policy.content)
        self.assertNotIn("score_rate", domain_payload(after_policy))
        self.assertNotIn("score", domain_payload(after_policy)["item_results"][0])

    def _session_from(self, response):
        from apps.assessment.models import ExperimentSession

        return ExperimentSession.objects.get(pk=domain_payload(response)["session_id"])
