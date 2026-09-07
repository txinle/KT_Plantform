import json
import uuid
from decimal import Decimal

from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.assessment.event_services import append_assessment_event
from apps.assessment.models import (
    Attempt,
    ExperimentSession,
    GradeRecord,
    StudentQuestionAssignment,
    SubmissionEvent,
)
from apps.experiments.models import Experiment, ExperimentQuestion
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.experiments.services import (
    _publish_legacy_experiment as publish_experiment,
    save_participant_rules,
)
from apps.question_bank.models import (
    Concept,
    Question,
    QuestionBank,
    QuestionConcept,
    QuestionOption,
)


@override_settings(CLASS_CENTER_ENABLED=True)
class TeacherDrilldownApiTests(TransactionTestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teacher-drilldown-api",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="深钻 API 课程",
            code="DRILL-API",
            term="2026 秋",
        )
        self.class_group = ClassGroup.objects.create(
            course=self.course,
            name="深钻 API 班",
            academic_year="2026",
        )
        self.current_student = self._enroll("DRILL-API-A", "当前学生")
        self.historical_student = self._enroll("DRILL-API-B", "历史学生")
        self.bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="深钻 API 题库",
        )
        concept = Concept.objects.create(course=self.course, name="KVL")
        question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SINGLE,
            text="KVL 单选诊断",
            correct_answer="B",
        )
        QuestionConcept.objects.create(
            question=question,
            concept=concept,
            is_primary=True,
        )
        for order, key in enumerate(("A", "B"), start=1):
            QuestionOption.objects.create(
                question=question,
                key=key,
                text=f"选项 {key}",
                display_order=order,
            )
        self.experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=self.bank,
            name="深钻 API 实验",
        )
        ExperimentQuestion.objects.create(
            experiment=self.experiment,
            question=question,
            display_order=1,
        )
        save_participant_rules(self.experiment, [self.class_group], [], [])
        self.version = publish_experiment(self.experiment, self.teacher)
        self.class_snapshot = self.version.class_snapshots.get(
            source_class_group=self.class_group
        )
        self.question_snapshot = self.version.question_snapshots.get()
        self.current_session = self._submit(
            self.current_student,
            states=["A", "B"],
            final_answer="B",
            is_correct=True,
        )
        self.historical_session = self._submit(
            self.historical_student,
            states=["A"],
            final_answer="A",
            is_correct=False,
        )
        CourseEnrollment.objects.filter(
            course=self.course,
            student=self.historical_student,
        ).update(is_active=False)
        self.client.force_login(self.teacher)

    def _enroll(self, student_number, display_name):
        student = User.objects.create_user(
            username=student_number,
            student_number=student_number,
            display_name=display_name,
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_group,
            student=student,
        )
        return student

    def _submit(self, student, *, states, final_answer, is_correct):
        session = ExperimentSession.objects.create(
            version=self.version,
            student=student,
            class_snapshot=self.class_snapshot,
            class_snapshot_name=self.class_snapshot.display_name,
            status=ExperimentSession.Status.COMPLETED,
            completed_at=timezone.now(),
        )
        assignment = StudentQuestionAssignment.objects.create(
            session=session,
            snapshot=self.question_snapshot,
            item_instance=get_or_create_default_item_instance(self.question_snapshot),
            assigned_sequence_index=1,
        )
        attempt = Attempt.objects.create(
            session=session,
            assignment=assignment,
            status=Attempt.Status.SUBMITTED,
            submitted_at=timezone.now(),
            first_to_submit_duration_ms=1000,
            final_answer=final_answer,
        )
        client_id = uuid.uuid4()
        origin = timezone.now()
        before = None
        for sequence, after in enumerate(states, start=1):
            append_assessment_event(
                event_type="response_state_changed",
                event_version=1,
                event_domain="assessment",
                actor_source="client",
                session=session,
                attempt=attempt,
                client_instance_id=client_id,
                client_sequence_no=sequence,
                client_monotonic_ms=sequence * 100,
                client_time_origin=origin,
                payload={
                    "question_type": self.question_snapshot.question_type,
                    "response_state_before": before,
                    "response_state_after": after,
                    "change_source": "student",
                },
            )
            before = after
        submission = SubmissionEvent.objects.create(
            attempt=attempt,
            submission_token=uuid.uuid4(),
            final_answer=final_answer,
            server_received_at=timezone.now(),
            committed_at=timezone.now(),
        )
        GradeRecord.objects.create(
            submission=submission,
            version=1,
            status=GradeRecord.Status.COMPLETED,
            validity_status=GradeRecord.ValidityStatus.EFFECTIVE,
            is_correct=is_correct,
            score=Decimal("1") if is_correct else Decimal("0"),
            max_score=Decimal("1"),
        )
        return session

    @property
    def api_prefix(self):
        return (
            f"/api/v1/teacher/courses/{self.course.pk}/classes/"
            f"{self.class_group.pk}/"
        )

    def test_api_projects_frozen_facts_actions_and_process_state(self):
        student = self.client.get(
            f"{self.api_prefix}students/{self.current_student.pk}/analysis/"
        )
        student_experiment = self.client.get(
            f"{self.api_prefix}students/{self.historical_student.pk}/"
            f"experiments/{self.experiment.pk}/"
        )
        question = self.client.get(
            f"{self.api_prefix}experiments/{self.experiment.pk}/questions/"
            f"{self.question_snapshot.pk}/"
        )
        process = self.client.get(
            f"{self.api_prefix}experiments/{self.experiment.pk}/questions/"
            f"{self.question_snapshot.pk}/students/{self.historical_student.pk}/process/"
        )

        for response in (student, student_experiment, question, process):
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()["course"]["id"], self.course.pk)
            self.assertEqual(response.json()["class_group"]["id"], self.class_group.pk)

        student_payload = student.json()
        self.assertEqual(student_payload["detail"]["student_id"], self.current_student.pk)
        self.assertEqual(student_payload["detail"]["experiments"][0]["detail_url"], reverse(
            "courses:class_student_experiment_detail",
            args=[
                self.course.pk,
                self.class_group.pk,
                self.current_student.pk,
                self.experiment.pk,
            ],
        ))
        self.assertIn("show_trend_chart", student_payload["detail"])

        student_experiment_payload = student_experiment.json()
        self.assertFalse(student_experiment_payload["detail"]["is_current_member"])
        question_row = student_experiment_payload["detail"]["questions"][0]
        self.assertTrue(question_row["process_complete"])
        self.assertEqual(question_row["question_url"], reverse(
            "courses:class_question_detail",
            args=[
                self.course.pk,
                self.class_group.pk,
                self.experiment.pk,
                self.question_snapshot.pk,
            ],
        ))

        question_payload = question.json()
        self.assertEqual(question_payload["detail"]["timing"]["quality"]["submitted_count"], 2)
        historical = next(
            row
            for row in question_payload["detail"]["participants"]
            if row["student_id"] == self.historical_student.pk
        )
        self.assertFalse(historical["is_current_member"])
        self.assertTrue(historical["process_complete"])

        process_payload = process.json()
        self.assertEqual(process_payload["detail"]["process"]["completeness"], "complete")
        self.assertIn("has_cross_instance_order_uncertainty", process_payload["detail"])
        self.assertIn("coverage_reason_labels", process_payload["detail"])
        serialized = json.dumps(process_payload)
        for forbidden in ("event_id", "client_instance_id", "raw payload", "content_hash"):
            self.assertNotIn(forbidden, serialized)

    def test_api_closes_flag_role_and_cross_scope_combinations(self):
        path = (
            f"{self.api_prefix}experiments/{self.experiment.pk}/questions/"
            f"{self.question_snapshot.pk}/"
        )
        outsider = User.objects.create_user(
            username="teacher-drilldown-api-outsider",
            role=User.Role.TEACHER,
        )
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(path).status_code, 404)

        self.client.force_login(self.teacher)
        self.assertEqual(
            self.client.get(
                f"{self.api_prefix}experiments/{self.experiment.pk}/questions/999999/"
            ).status_code,
            404,
        )
        with self.settings(CLASS_CENTER_ENABLED=False):
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_result_and_session_urls_receive_backend_resolved_destinations(self):
        results = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/experiments/"
            f"{self.experiment.pk}/results/resolve/"
        )
        session = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/experiments/"
            f"{self.experiment.pk}/sessions/{self.current_session.pk}/resolve/"
        )

        self.assertEqual(results.status_code, 200, results.content)
        self.assertEqual(results.json()["kind"], "canonical")
        self.assertEqual(
            results.json()["canonical_url"],
            (
                reverse(
                    "courses:class_detail",
                    args=[self.course.pk, self.class_group.pk],
                )
                + f"?section=experiments&experiment_id={self.experiment.pk}"
            ),
        )
        self.assertEqual(session.status_code, 200, session.content)
        self.assertEqual(session.json()["kind"], "canonical")
        self.assertEqual(
            session.json()["canonical_url"],
            reverse(
                "courses:class_student_experiment_detail",
                args=[
                    self.course.pk,
                    self.class_group.pk,
                    self.current_student.pk,
                    self.experiment.pk,
                ],
            ),
        )

    def test_result_compatibility_retains_legacy_sessions_and_safe_report_data(self):
        results_url = (
            f"/api/v1/teacher/courses/{self.course.pk}/experiments/"
            f"{self.experiment.pk}/results/resolve/"
        )
        session_url = (
            f"/api/v1/teacher/courses/{self.course.pk}/experiments/"
            f"{self.experiment.pk}/sessions/{self.current_session.pk}/resolve/"
        )

        with self.settings(CLASS_CENTER_ENABLED=False):
            results = self.client.get(results_url, {"status": "completed"})
            session = self.client.get(session_url)

        self.assertEqual(results.status_code, 200, results.content)
        self.assertEqual(results.json()["kind"], "sessions")
        self.assertEqual(results.json()["selected_status"], "completed")
        self.assertEqual(len(results.json()["sessions"]), 2)
        self.assertEqual(results.json()["sessions"][0]["status"], "completed")
        self.assertEqual(session.status_code, 200, session.content)
        self.assertEqual(session.json()["kind"], "legacy_report")
        self.assertEqual(session.json()["report"]["student_name"], "当前学生")
        self.assertEqual(
            session.json()["report"]["questions"][0]["stable_code"],
            self.question_snapshot.stable_code,
        )
        self.assertTrue(session.json()["actions"]["word_url"].endswith("/word/"))
