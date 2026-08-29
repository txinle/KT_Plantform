import json
import uuid
from io import StringIO

from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.assessment.models import (
    Attempt,
    ExperimentSession,
    GradeRecord,
    StudentQuestionAssignment,
    SubmissionEvent,
)
from apps.question_bank.models import Question, QuestionBank

from .class_task_audit import audit_class_experiment_tasks
from .management.commands.audit_class_experiment_tasks import _table_value
from .models import (
    ClassExperimentTask,
    Experiment,
    ExperimentClassSnapshot,
    ExperimentVersion,
    ExperimentVersionParticipantSnapshot,
    ItemInstance,
    QuestionSnapshot,
    canonical_presentation_hash,
)


class ClassExperimentTaskAuditTests(TransactionTestCase):
    """The audit is an advanced, read-only diagnosis of historical task facts."""

    _DEFAULT_CLASS_GROUP = object()

    def setUp(self):
        self.teacher = User.objects.create_user(
            username="audit-teacher",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="审计课程",
            code="AUDIT101",
            term="2026 秋",
        )
        self.other_course = Course.objects.create(
            owner=self.teacher,
            name="跨课程来源",
            code="AUDIT102",
            term="2026 秋",
        )
        self.class_group = ClassGroup.objects.create(
            course=self.course,
            name="审计班",
            academic_year="2023",
        )
        self.other_class_group = ClassGroup.objects.create(
            course=self.other_course,
            name="跨课程班",
            academic_year="2023",
        )
        self.peer_class_group = ClassGroup.objects.create(
            course=self.course,
            name="同版本对照班",
            academic_year="2023",
        )
        self.student = User.objects.create_user(
            username="audit-sensitive-username",
            display_name="敏感学生姓名",
            student_number="AUDIT-SENSITIVE-001",
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_group,
            student=self.student,
        )
        self.peer_student = User.objects.create_user(
            username="audit-peer-student",
            student_number="AUDIT-PEER-001",
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.peer_class_group,
            student=self.peer_student,
        )

        self.good_experiment, self.good_version, self.good_snapshot = (
            self._experiment_version_snapshot("正式任务")
        )
        self._insert_historical_task(
            class_group=self.class_group,
            experiment=self.good_experiment,
            version=self.good_version,
            snapshot=self.good_snapshot,
        )

        self.missing_experiment, self.missing_version, self.missing_snapshot = (
            self._experiment_version_snapshot("缺少任务")
        )
        self._add_scoped_facts(
            version=self.missing_version,
            class_snapshot=self.missing_snapshot,
            experiment=self.missing_experiment,
        )
        missing_peer_snapshot = ExperimentClassSnapshot.objects.create(
            version=self.missing_version,
            source_class_group=self.peer_class_group,
            name_snapshot=self.peer_class_group.name,
            academic_year_snapshot=self.peer_class_group.academic_year,
            capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
        )
        self._add_scoped_facts(
            version=self.missing_version,
            class_snapshot=missing_peer_snapshot,
            experiment=self.missing_experiment,
            student=self.peer_student,
        )

        self.multi_experiment, self.multi_first_version, self.multi_first_snapshot = (
            self._experiment_version_snapshot("多发布快照")
        )
        self.multi_second_version, self.multi_second_snapshot = self._version_snapshot(
            self.multi_experiment,
            class_group=self.class_group,
        )
        self._insert_historical_task(
            class_group=self.class_group,
            experiment=self.multi_experiment,
            version=self.multi_first_version,
            snapshot=self.multi_first_snapshot,
        )
        cross_version_session = ExperimentSession.objects.create(
            version=self.multi_second_version,
            student=self.student,
            class_snapshot=self.multi_first_snapshot,
            status=ExperimentSession.Status.COMPLETED,
        )
        self._add_scoped_facts(
            version=self.multi_second_version,
            class_snapshot=self.multi_first_snapshot,
            experiment=self.multi_experiment,
            student=self.student,
            session=cross_version_session,
        )

        self.null_experiment, self.null_version, self.null_snapshot = (
            self._experiment_version_snapshot(
                "历史标签",
                class_group=None,
                label="旧系统班级标签",
            )
        )
        self._add_scoped_facts(
            version=self.null_version,
            class_snapshot=self.null_snapshot,
            experiment=self.null_experiment,
        )

        self.cross_experiment, self.cross_version, self.cross_snapshot = (
            self._experiment_version_snapshot(
                "跨课程快照",
                class_group=self.other_class_group,
            )
        )
        self._insert_historical_task(
            class_group=self.other_class_group,
            experiment=self.cross_experiment,
            version=self.cross_version,
            snapshot=self.cross_snapshot,
        )

        self.alignment_experiment, self.alignment_version, self.alignment_snapshot = (
            self._experiment_version_snapshot("任务不对齐")
        )
        other_release_experiment, other_release_version, other_release_snapshot = (
            self._experiment_version_snapshot("错误发布快照")
        )
        self._insert_historical_task(
            class_group=self.class_group,
            experiment=other_release_experiment,
            version=other_release_version,
            snapshot=other_release_snapshot,
        )
        self._insert_historical_task(
            class_group=self.class_group,
            experiment=self.alignment_experiment,
            version=other_release_version,
            snapshot=self.alignment_snapshot,
            published_at=self.alignment_version.published_at,
        )

    def _experiment_version_snapshot(
        self,
        suffix,
        *,
        class_group=_DEFAULT_CLASS_GROUP,
        label="",
    ):
        experiment = self._experiment(suffix)
        version, snapshot = self._version_snapshot(
            experiment,
            class_group=(
                self.class_group
                if class_group is self._DEFAULT_CLASS_GROUP
                else class_group
            ),
            label=label,
        )
        return experiment, version, snapshot

    def _experiment(self, suffix):
        bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name=f"{suffix}题库",
        )
        return Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=bank,
            name=f"{suffix}实验",
            status=Experiment.Status.PUBLISHED,
        )

    def _version_snapshot(self, experiment, *, class_group, label=""):
        version = ExperimentVersion.objects.create(
            experiment=experiment,
            version_number=ExperimentVersion.objects.filter(
                experiment=experiment
            ).count()
            + 1,
            published_by=self.teacher,
        )
        if class_group is None:
            snapshot = ExperimentClassSnapshot.objects.create(
                version=version,
                name_snapshot=label,
                academic_year_snapshot="",
                capture_mode=(
                    ExperimentClassSnapshot.CaptureMode.LEGACY_LABEL_PROJECTION
                ),
            )
        else:
            snapshot = ExperimentClassSnapshot.objects.create(
                version=version,
                source_class_group=class_group,
                name_snapshot=class_group.name,
                academic_year_snapshot=class_group.academic_year,
                capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
            )
        return version, snapshot

    def _insert_historical_task(
        self,
        *,
        class_group,
        experiment,
        version,
        snapshot,
        published_at=None,
    ):
        table = connection.ops.quote_name(ClassExperimentTask._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {table}
                    (class_group_id, experiment_id, release_snapshot_id,
                     class_snapshot_id, published_at, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                [
                    class_group.pk,
                    experiment.pk,
                    version.pk,
                    snapshot.pk.hex,
                    published_at or version.published_at,
                    timezone.now(),
                ],
            )

    def _add_scoped_facts(
        self,
        *,
        version,
        class_snapshot,
        experiment,
        student=None,
        session=None,
    ):
        student = student or self.student
        ExperimentVersionParticipantSnapshot.objects.create(
            version=version,
            student=student,
            class_snapshot=class_snapshot,
        )
        if session is None:
            session = ExperimentSession.objects.create(
                version=version,
                student=student,
                class_snapshot=class_snapshot,
                status=ExperimentSession.Status.COMPLETED,
            )
        sequence = Question.objects.filter(bank=experiment.question_bank).count() + 1
        source_question = Question.objects.create(
            bank=experiment.question_bank,
            sequence=sequence,
            question_type=Question.Type.SHORT,
            text=f"{experiment.name}事实题",
            correct_answer="正确",
        )
        question_snapshot = QuestionSnapshot.objects.create(
            version=version,
            source_question=source_question,
            source_sequence=sequence,
            code=f"Q{sequence:04d}",
            question_type=Question.Type.SHORT,
            text="事实题",
            correct_answer="正确",
            primary_kc="审计 KC",
            all_kcs=["审计 KC"],
        )
        presentation_hash = canonical_presentation_hash(
            presentation_variant="default",
            option_order=[],
            parameter_values={},
            random_seed="",
        )
        item_instance = ItemInstance.objects.create(
            question_snapshot=question_snapshot,
            presentation_hash=presentation_hash,
        )
        assignment = StudentQuestionAssignment.objects.create(
            session=session,
            snapshot=question_snapshot,
            item_instance=item_instance,
            assigned_sequence_index=1,
        )
        attempt = Attempt.objects.create(
            session=session,
            assignment=assignment,
            status=Attempt.Status.SUBMITTED,
        )
        submitted_at = timezone.now()
        submission = SubmissionEvent.objects.create(
            attempt=attempt,
            submission_token=uuid.uuid4(),
            final_answer={"answer": "绝不可泄露的答案"},
            server_received_at=submitted_at,
            committed_at=submitted_at,
        )
        GradeRecord.objects.create(
            submission=submission,
            version=1,
            status=GradeRecord.Status.COMPLETED,
            validity_status=GradeRecord.ValidityStatus.EFFECTIVE,
            is_correct=True,
            score=1,
            max_score=1,
            normalized_response={"answer": "绝不可泄露的规范答案"},
        )

    def _row_for_reason(
        self,
        reason_code,
        *,
        experiment_id=None,
        class_group_id=None,
    ):
        rows = [
            row
            for row in audit_class_experiment_tasks()
            if reason_code in row.reason_codes
            and (experiment_id is None or row.experiment_id == experiment_id)
            and (class_group_id is None or row.class_group_id == class_group_id)
        ]
        self.assertEqual(len(rows), 1, rows)
        return rows[0]

    def test_audit_reports_historical_conflicts_and_scoped_fact_counts(self):
        rows = audit_class_experiment_tasks()

        self.assertFalse(
            any(row.experiment_id == self.good_experiment.pk for row in rows)
        )

        missing = self._row_for_reason(
            "missing_eligible_task",
            class_group_id=self.class_group.pk,
        )
        self.assertEqual(missing.experiment_id, self.missing_experiment.pk)
        self.assertEqual(missing.release_snapshot_count, 1)
        self.assertEqual(missing.class_snapshot_count, 1)
        self.assertEqual(missing.participant_count, 1)
        self.assertEqual(missing.session_count, 1)
        self.assertEqual(missing.attempt_count, 1)
        self.assertEqual(missing.grade_record_count, 1)

        multi = self._row_for_reason("multiple_release_snapshots")
        self.assertEqual(multi.experiment_id, self.multi_experiment.pk)
        self.assertEqual(multi.release_snapshot_count, 2)
        self.assertEqual(multi.class_snapshot_count, 2)
        self.assertEqual(multi.participant_count, 0)
        self.assertEqual(multi.session_count, 0)
        self.assertEqual(multi.attempt_count, 0)
        self.assertEqual(multi.grade_record_count, 0)
        self.assertEqual(multi.reason_codes, ("multiple_release_snapshots",))

        source_null = self._row_for_reason("source_class_missing")
        self.assertEqual(source_null.experiment_id, self.null_experiment.pk)
        self.assertIsNone(source_null.class_group_id)
        self.assertEqual(source_null.class_label, "旧系统班级标签")
        self.assertEqual(source_null.participant_count, 1)
        self.assertEqual(source_null.session_count, 1)
        self.assertEqual(source_null.attempt_count, 1)
        self.assertEqual(source_null.grade_record_count, 1)

        cross_course = self._row_for_reason("course_mismatch")
        self.assertEqual(cross_course.experiment_id, self.cross_experiment.pk)
        self.assertEqual(cross_course.class_group_id, self.other_class_group.pk)
        self.assertEqual(cross_course.course_id, self.course.pk)
        self.assertEqual(cross_course.class_course_id, self.other_course.pk)
        self.assertEqual(
            cross_course.reason_codes,
            ("course_mismatch", "task_alignment_mismatch"),
        )

        task_mismatch = self._row_for_reason(
            "task_alignment_mismatch",
            experiment_id=self.alignment_experiment.pk,
        )
        self.assertEqual(task_mismatch.experiment_id, self.alignment_experiment.pk)

    def test_audit_reports_multiple_class_snapshots_for_one_release_as_a_mismatch(self):
        duplicate_experiment, version, snapshot = self._experiment_version_snapshot(
            "重复班级快照"
        )
        self._insert_historical_task(
            class_group=self.class_group,
            experiment=duplicate_experiment,
            version=version,
            snapshot=snapshot,
        )
        constraint = next(
            constraint
            for constraint in ExperimentClassSnapshot._meta.constraints
            if constraint.name == "unique_version_source_class_snapshot"
        )
        duplicate = None
        with connection.schema_editor() as schema_editor:
            schema_editor.remove_constraint(ExperimentClassSnapshot, constraint)
        try:
            duplicate = ExperimentClassSnapshot.objects.create(
                version=version,
                source_class_group=self.class_group,
                name_snapshot="重复班级快照",
                academic_year_snapshot="2023",
                capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
            )

            snapshot_mismatch = self._row_for_reason("snapshot_count_mismatch")
            self.assertEqual(snapshot_mismatch.experiment_id, duplicate_experiment.pk)
            self.assertEqual(snapshot_mismatch.release_snapshot_count, 1)
            self.assertEqual(snapshot_mismatch.class_snapshot_count, 2)
            self.assertEqual(snapshot_mismatch.reason_codes, ("snapshot_count_mismatch",))
        finally:
            if duplicate is not None:
                ExperimentClassSnapshot.objects.filter(pk=duplicate.pk).delete()
            with connection.schema_editor() as schema_editor:
                schema_editor.add_constraint(ExperimentClassSnapshot, constraint)

    def test_command_is_stable_read_only_and_uses_the_allow_list(self):
        count_models = (
            ClassExperimentTask,
            ExperimentVersion,
            ExperimentClassSnapshot,
            ExperimentVersionParticipantSnapshot,
            ExperimentSession,
            Attempt,
            GradeRecord,
        )
        before = {model._meta.label: model.objects.count() for model in count_models}

        json_first = StringIO()
        call_command("audit_class_experiment_tasks", format="json", stdout=json_first)
        json_second = StringIO()
        call_command("audit_class_experiment_tasks", format="json", stdout=json_second)
        table_first = StringIO()
        call_command("audit_class_experiment_tasks", stdout=table_first)
        table_second = StringIO()
        call_command("audit_class_experiment_tasks", stdout=table_second)

        self.assertEqual(json_first.getvalue().encode("utf-8"), json_second.getvalue().encode("utf-8"))
        self.assertEqual(table_first.getvalue(), table_second.getvalue())
        self.assertIn("冲突原因", table_first.getvalue())

        payload = json.loads(json_first.getvalue())
        self.assertIsInstance(payload, list)
        self.assertGreater(len(payload), 0)
        self.assertEqual(
            set(payload[0]),
            {
                "class_course_id",
                "class_group_id",
                "class_label",
                "class_snapshot_count",
                "course_id",
                "course_label",
                "experiment_id",
                "experiment_label",
                "grade_record_count",
                "participant_count",
                "reason_codes",
                "release_snapshot_count",
                "session_count",
                "attempt_count",
            },
        )
        rendered_json = json_first.getvalue()
        for forbidden in (
            "敏感学生姓名",
            "audit-sensitive-username",
            "AUDIT-SENSITIVE-001",
            "绝不可泄露的答案",
            "绝不可泄露的规范答案",
            "final_answer",
            "normalized_response",
            "payload",
        ):
            self.assertNotIn(forbidden, rendered_json)

        after = {model._meta.label: model.objects.count() for model in count_models}
        self.assertEqual(after, before)

    def test_audit_uses_a_constant_query_budget_for_many_historical_snapshots(self):
        experiment = self._experiment("大量历史标签")
        version = ExperimentVersion.objects.create(
            experiment=experiment,
            version_number=1,
            published_by=self.teacher,
        )
        ExperimentClassSnapshot.objects.bulk_create(
            [
                ExperimentClassSnapshot(
                    version=version,
                    name_snapshot=f"历史标签 {index}",
                    academic_year_snapshot="",
                    capture_mode=(
                        ExperimentClassSnapshot.CaptureMode.LEGACY_LABEL_PROJECTION
                    ),
                )
                for index in range(1000)
            ]
        )

        with CaptureQueriesContext(connection) as queries:
            rows = audit_class_experiment_tasks()

        self.assertLessEqual(len(queries), 6)
        self.assertGreaterEqual(
            sum(
                1
                for row in rows
                if row.experiment_id == experiment.pk
                and row.reason_codes == ("source_class_missing",)
            ),
            1000,
        )

    def test_table_cells_escape_separators_and_remove_control_characters(self):
        self.assertEqual(
            _table_value("班|级\x00\n\t\x7f\x9b\u202e\u200b保留😀"),
            r"班\|级保留😀",
        )
