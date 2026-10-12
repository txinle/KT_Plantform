from apps.api.test_support import domain_payload
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from apps.accounts.models import ClassGroup, Course, CourseTeacher, User
from apps.audit.models import AuditLog
from apps.experiments.models import (
    ClassExperimentTask,
    Experiment,
    ExperimentClassSnapshot,
    ExperimentVersion,
    QuestionSnapshot,
)
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.experiments.publication_services import _class_experiment_task_publication_scope
from apps.question_bank.models import Question, QuestionBank

from .manual_grading_services import ManualGradingError, grade_task_short_answer
from .models import (
    Attempt,
    ExperimentSession,
    GradeRecord,
    StudentQuestionAssignment,
    SubmissionEvent,
)


class ManualShortAnswerGradingTests(TransactionTestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="manual-grading-teacher",
            password="test-password",
            role=User.Role.TEACHER,
        )
        self.other_teacher = User.objects.create_user(
            username="manual-grading-outsider",
            password="test-password",
            role=User.Role.TEACHER,
        )
        self.student = User.objects.create_user(
            username="manual-grading-student",
            student_number="S-001",
            display_name="学生甲",
            role=User.Role.STUDENT,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="人工批改课程",
            code="MANUAL-1",
            term="2026 秋",
        )
        self.class_group = ClassGroup.objects.create(
            course=self.course,
            name="一班",
            academic_year="2026",
        )
        self.bank = QuestionBank.objects.create(
            course=self.course,
            owner=self.teacher,
            name="人工批改题库",
        )
        self.question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            code="SA-1",
            question_type=Question.Type.SHORT,
            text="说明变量之间的关系。",
            correct_answer="参考答案",
            explanation="参考解析",
        )
        self.experiment = Experiment.objects.create(
            course=self.course,
            owner=self.teacher,
            question_bank=self.bank,
            name="人工批改实验",
        )
        self.version = ExperimentVersion.objects.create(
            experiment=self.experiment,
            version_number=1,
            configuration={"short_answer_grading_policy": "manual_binary_v1"},
            published_by=self.teacher,
        )
        self.snapshot = QuestionSnapshot.objects.create(
            version=self.version,
            source_question=self.question,
            source_sequence=1,
            code="SA-1",
            question_type="short_answer",
            text=self.question.text,
            correct_answer=self.question.correct_answer,
            explanation=self.question.explanation,
            primary_kc="KC",
        )
        class_snapshot = ExperimentClassSnapshot.objects.create(
            version=self.version,
            source_class_group=self.class_group,
            name_snapshot=self.class_group.name,
            academic_year_snapshot=self.class_group.academic_year,
            capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
        )
        with transaction.atomic():
            with _class_experiment_task_publication_scope():
                self.task = ClassExperimentTask.objects.create(
                    class_group=self.class_group,
                    experiment=self.experiment,
                    release_snapshot=self.version,
                    class_snapshot=class_snapshot,
                    published_at=self.version.published_at,
                    published_by=self.teacher,
                )
        session = ExperimentSession.objects.create(
            version=self.version,
            class_task=self.task,
            student=self.student,
        )
        assignment = StudentQuestionAssignment.objects.create(
            session=session,
            snapshot=self.snapshot,
            item_instance=get_or_create_default_item_instance(self.snapshot),
            assigned_sequence_index=1,
        )
        attempt = Attempt.objects.create(
            session=session,
            assignment=assignment,
            status=Attempt.Status.SUBMITTED,
            final_answer="学生自己的解释",
            submitted_at=timezone.now(),
        )
        self.submission = SubmissionEvent.objects.create(
            attempt=attempt,
            submission_token=uuid.uuid4(),
            final_answer=attempt.final_answer,
            grading_status=SubmissionEvent.GradingStatus.PENDING,
            server_received_at=timezone.now(),
            committed_at=timezone.now(),
        )
        GradeRecord.objects.create(
            submission=self.submission,
            version=1,
            status=GradeRecord.Status.PENDING,
            validity_status=GradeRecord.ValidityStatus.CANDIDATE,
            max_score=1,
            grading_method="manual_binary_v1",
            grading_version="manual_binary_v1",
        )

    def test_service_appends_binary_grade_and_requires_reason_for_regrade(self):
        grade = grade_task_short_answer(
            task_id=self.task.pk,
            submission_id=self.submission.pk,
            actor=self.teacher,
            expected_grade_version=1,
            is_correct=True,
            comment="",
        )

        self.assertEqual(grade.version, 2)
        self.assertEqual(str(grade.score), "1")
        self.assertEqual(str(grade.max_score), "1")
        self.assertEqual(grade.grading_method, "manual_binary_v1")
        self.assertTrue(grade.is_correct)
        self.submission.refresh_from_db()
        self.assertTrue(self.submission.is_correct)
        self.assertEqual(self.submission.grading_status, "completed")

        with self.assertRaises(ManualGradingError) as raised:
            grade_task_short_answer(
                task_id=self.task.pk,
                submission_id=self.submission.pk,
                actor=self.teacher,
                expected_grade_version=2,
                is_correct=False,
                comment="  ",
            )
        self.assertEqual(raised.exception.code, "INVALID_REGRADING_REASON")

        replacement = grade_task_short_answer(
            task_id=self.task.pk,
            submission_id=self.submission.pk,
            actor=self.teacher,
            expected_grade_version=2,
            is_correct=False,
            comment="复核后更正",
        )
        self.assertEqual(replacement.version, 3)
        self.assertFalse(replacement.is_correct)
        self.assertEqual(str(replacement.score), "0")
        self.assertEqual(
            list(self.submission.grade_records.order_by("version").values_list("version", flat=True)),
            [1, 2, 3],
        )
        audit = AuditLog.objects.filter(
            action="class_task.short_answer_graded",
            object_id=str(self.submission.pk),
        ).order_by("-created_at").first()
        self.assertNotIn("学生自己的解释", str(audit.details))

    def test_service_rejects_stale_version_and_outsider(self):
        with self.assertRaises(ManualGradingError) as raised:
            grade_task_short_answer(
                task_id=self.task.pk,
                submission_id=self.submission.pk,
                actor=self.teacher,
                expected_grade_version=9,
                is_correct=True,
                comment="",
            )
        self.assertEqual(raised.exception.code, "GRADE_VERSION_CONFLICT")

        with self.assertRaises(ManualGradingError) as raised:
            grade_task_short_answer(
                task_id=self.task.pk,
                submission_id=self.submission.pk,
                actor=self.other_teacher,
                expected_grade_version=1,
                is_correct=True,
                comment="",
            )
        self.assertEqual(raised.exception.code, "SUBMISSION_SCOPE_MISMATCH")

    def test_teacher_api_lists_and_grades_real_name_submission(self):
        self.client.force_login(self.teacher)

        grading_page = self.client.get(
            f"/teacher/courses/{self.course.pk}/experiments/{self.experiment.pk}/"
            f"class-tasks/{self.task.pk}/grading/"
        )
        self.assertEqual(grading_page.status_code, 404, grading_page.content)

        task_detail = self.client.get(
            f"/api/v1/teacher/class-tasks/{self.task.pk}/"
        )
        self.assertEqual(task_detail.status_code, 200, task_detail.content)
        self.assertEqual(domain_payload(task_detail)["grading"]["pending"], 1)
        self.assertIn(
            "grade_short_answers",
            domain_payload(task_detail)["available_actions"],
        )

        overview = self.client.get(f"/api/v1/teacher/class-tasks/{self.task.pk}/grading/")
        self.assertEqual(overview.status_code, 200, overview.content)
        self.assertEqual(domain_payload(overview)["totals"]["pending"], 1)
        self.assertEqual(domain_payload(overview)["questions"][0]["question_snapshot_id"], self.snapshot.pk)

        detail = self.client.get(
            f"/api/v1/teacher/class-tasks/{self.task.pk}/grading/questions/{self.snapshot.pk}/"
        )
        self.assertEqual(detail.status_code, 200, detail.content)
        row = domain_payload(detail)["submissions"]["items"][0]
        self.assertEqual(row["display_name"], "学生甲")
        self.assertEqual(row["student_number"], "S-001")
        self.assertEqual(row["final_answer"], "学生自己的解释")
        self.assertEqual(row["latest_grade_version"], 1)

        response = self.client.post(
            f"/api/v1/teacher/class-tasks/{self.task.pk}/grading/submissions/{self.submission.pk}/",
            data={"expected_grade_version": 1, "is_correct": True, "comment": ""},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(domain_payload(response)["submission"]["current_is_correct"])
        self.assertEqual(domain_payload(response)["task_progress"]["pending"], 0)

    def test_postgresql_concurrent_grading_accepts_only_one_expected_version(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL row-lock semantics are required.")
        collaborator = User.objects.create_user(
            username="manual-grading-collaborator",
            role=User.Role.TEACHER,
        )
        CourseTeacher.objects.create(course=self.course, teacher=collaborator)
        barrier = Barrier(2)

        def grade(actor_id, is_correct):
            close_old_connections()
            actor = User.objects.get(pk=actor_id)
            barrier.wait()
            try:
                grade_task_short_answer(
                    task_id=self.task.pk,
                    submission_id=self.submission.pk,
                    actor=actor,
                    expected_grade_version=1,
                    is_correct=is_correct,
                    comment="",
                )
                return "completed"
            except ManualGradingError as exc:
                return exc.code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda args: grade(*args),
                    [(self.teacher.pk, True), (collaborator.pk, False)],
                )
            )

        self.assertCountEqual(results, ["completed", "GRADE_VERSION_CONFLICT"])
        self.assertEqual(
            self.submission.grade_records.filter(
                validity_status=GradeRecord.ValidityStatus.EFFECTIVE
            ).count(),
            1,
        )
