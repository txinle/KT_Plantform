from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.audit.models import AuditLog
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept

from .models import (
    Experiment,
    ExperimentParticipantClass,
    ExperimentParticipantOverride,
    ExperimentQuestion,
    ExperimentStudent,
)
from .services import assign_students, publish_experiment, save_participant_rules


ROOT = Path(__file__).resolve().parents[2]


class ParticipantRuleModelTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="participant-rule-teacher",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="参与规则课程",
            code="PARTICIPANT-RULES",
            term="2026 秋",
        )
        self.class_group = ClassGroup.objects.create(course=self.course, name="规则班")
        self.student = User.objects.create_user(
            username="PARTICIPANT-RULE-STUDENT",
            student_number="PARTICIPANT-RULE-STUDENT",
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_group,
            student=self.student,
        )
        bank = QuestionBank.objects.create(
            owner=self.teacher,
            course=self.course,
            name="参与规则题库",
        )
        self.experiment = Experiment.objects.create(
            owner=self.teacher,
            created_by=self.teacher,
            course=self.course,
            question_bank=bank,
            name="参与规则实验",
        )

    def test_participant_rule_rows_are_unique_per_experiment_target(self):
        ExperimentParticipantClass.objects.create(
            experiment=self.experiment,
            class_group=self.class_group,
        )
        ExperimentParticipantOverride.objects.create(
            experiment=self.experiment,
            student=self.student,
            mode=ExperimentParticipantOverride.Mode.INCLUDE,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExperimentParticipantClass.objects.create(
                    experiment=self.experiment,
                    class_group=self.class_group,
                )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExperimentParticipantOverride.objects.create(
                    experiment=self.experiment,
                    student=self.student,
                    mode=ExperimentParticipantOverride.Mode.EXCLUDE,
                )

    def test_rule_clean_requires_an_active_draft_course_student_and_class(self):
        other_course = Course.objects.create(
            owner=self.teacher,
            name="其他参与规则课程",
            code="OTHER-PARTICIPANT-RULES",
            term="2026 秋",
        )
        other_class = ClassGroup.objects.create(course=other_course, name="其他规则班")

        with self.assertRaisesMessage(ValidationError, "实验所属课程"):
            ExperimentParticipantClass(
                experiment=self.experiment,
                class_group=other_class,
            ).full_clean()

        self.class_group.is_active = False
        self.class_group.save(update_fields=["is_active"])
        with self.assertRaisesMessage(ValidationError, "停用"):
            ExperimentParticipantClass(
                experiment=self.experiment,
                class_group=self.class_group,
            ).full_clean()

        self.class_group.is_active = True
        self.class_group.save(update_fields=["is_active"])
        self.student.is_active = False
        self.student.save(update_fields=["is_active"])
        with self.assertRaisesMessage(ValidationError, "在读学生"):
            ExperimentParticipantOverride(
                experiment=self.experiment,
                student=self.student,
                mode=ExperimentParticipantOverride.Mode.INCLUDE,
            ).full_clean()

        self.student.is_active = True
        self.student.save(update_fields=["is_active"])
        self.experiment.status = Experiment.Status.PUBLISHED
        self.experiment.save(update_fields=["status"])
        with self.assertRaisesMessage(ValidationError, "发布后"):
            ExperimentParticipantOverride(
                experiment=self.experiment,
                student=self.student,
                mode=ExperimentParticipantOverride.Mode.INCLUDE,
            ).full_clean()


class ParticipantRuleMigrationTests(TransactionTestCase):
    reset_sequences = True
    migrate_from = ("experiments", "0004_experiment_selection_mode_and_more")
    migrate_to = ("experiments", "0005_experiment_participant_rules")

    @staticmethod
    def targets_with_experiments_at(executor, experiments_target):
        return [
            experiments_target if target[0] == "experiments" else target
            for target in executor.loader.graph.leaf_nodes()
        ]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.executor = MigrationExecutor(connection)
        cls.current_targets = cls.executor.loader.graph.leaf_nodes()
        cls.old_targets = cls.targets_with_experiments_at(cls.executor, cls.migrate_from)
        cls.executor.migrate(cls.old_targets)
        old_apps = cls.executor.loader.project_state(cls.old_targets).apps

        User = old_apps.get_model("accounts", "User")
        Course = old_apps.get_model("accounts", "Course")
        ClassGroup = old_apps.get_model("accounts", "ClassGroup")
        CourseEnrollment = old_apps.get_model("accounts", "CourseEnrollment")
        QuestionBank = old_apps.get_model("question_bank", "QuestionBank")
        Experiment = old_apps.get_model("experiments", "Experiment")
        ExperimentStudent = old_apps.get_model("experiments", "ExperimentStudent")

        teacher = User.objects.create(username="participant-migration-teacher", role="teacher")
        course = Course.objects.create(
            owner_id=teacher.pk,
            name="历史参与规则课程",
            code="HIST-PARTICIPANT",
            term="2026 秋",
        )
        class_group = ClassGroup.objects.create(course_id=course.pk, name="历史班")
        student = User.objects.create(
            username="PARTICIPANT-MIGRATION-STUDENT",
            student_number="PARTICIPANT-MIGRATION-STUDENT",
            role="student",
        )
        CourseEnrollment.objects.create(
            course_id=course.pk,
            class_group_id=class_group.pk,
            student_id=student.pk,
            is_active=True,
        )
        bank = QuestionBank.objects.create(
            owner_id=teacher.pk,
            created_by_id=teacher.pk,
            course_id=course.pk,
            name="历史参与规则题库",
        )
        draft = Experiment.objects.create(
            owner_id=teacher.pk,
            created_by_id=teacher.pk,
            course_id=course.pk,
            question_bank_id=bank.pk,
            name="历史草稿",
            status="draft",
        )
        published = Experiment.objects.create(
            owner_id=teacher.pk,
            created_by_id=teacher.pk,
            course_id=course.pk,
            question_bank_id=bank.pk,
            name="历史已发布",
            status="published",
        )
        closed = Experiment.objects.create(
            owner_id=teacher.pk,
            created_by_id=teacher.pk,
            course_id=course.pk,
            question_bank_id=bank.pk,
            name="历史已关闭",
            status="closed",
        )
        archived = Experiment.objects.create(
            owner_id=teacher.pk,
            created_by_id=teacher.pk,
            course_id=course.pk,
            question_bank_id=bank.pk,
            name="历史已归档",
            status="archived",
        )
        ExperimentStudent.objects.create(experiment_id=draft.pk, student_id=student.pk)
        ExperimentStudent.objects.create(experiment_id=published.pk, student_id=student.pk)
        ExperimentStudent.objects.create(experiment_id=closed.pk, student_id=student.pk)
        ExperimentStudent.objects.create(experiment_id=archived.pk, student_id=student.pk)
        cls.draft_pk = draft.pk
        cls.published_pk = published.pk
        cls.closed_pk = closed.pk
        cls.archived_pk = archived.pk
        cls.student_pk = student.pk

        cls.executor = MigrationExecutor(connection)
        cls.new_targets = cls.targets_with_experiments_at(cls.executor, cls.migrate_to)
        cls.executor.migrate(cls.new_targets)
        cls.apps = cls.executor.loader.project_state(cls.new_targets).apps

    @classmethod
    def tearDownClass(cls):
        try:
            MigrationExecutor(connection).migrate(cls.current_targets)
        finally:
            super().tearDownClass()

    def test_only_legacy_draft_roster_becomes_explicit_include_rules(self):
        ExperimentStudent = self.apps.get_model("experiments", "ExperimentStudent")
        ExperimentParticipantOverride = self.apps.get_model(
            "experiments",
            "ExperimentParticipantOverride",
        )

        self.assertEqual(
            list(
                ExperimentParticipantOverride.objects.filter(
                    experiment_id=self.draft_pk
                ).values_list("student_id", "mode")
            ),
            [(self.student_pk, "include")],
        )
        self.assertFalse(
            ExperimentParticipantOverride.objects.filter(
                experiment_id=self.published_pk
            ).exists()
        )
        self.assertFalse(
            ExperimentParticipantOverride.objects.filter(
                experiment_id__in=[self.closed_pk, self.archived_pk]
            ).exists()
        )
        self.assertEqual(
            ExperimentStudent.objects.filter(experiment_id=self.draft_pk).count(),
            1,
        )
        self.assertEqual(
            ExperimentStudent.objects.filter(experiment_id=self.published_pk).count(),
            1,
        )
        self.assertEqual(
            ExperimentStudent.objects.filter(
                experiment_id__in=[self.closed_pk, self.archived_pk]
            ).count(),
            2,
        )


class ParticipantRuleSaveServiceTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="participant-save-teacher",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="规则保存课程",
            code="SAVE-PARTICIPANTS",
            term="2026 秋",
        )
        self.class_a = ClassGroup.objects.create(
            course=self.course,
            academic_year="2026",
            name="甲班",
        )
        self.class_b = ClassGroup.objects.create(
            course=self.course,
            academic_year="2026",
            name="乙班",
        )
        self.other_course = Course.objects.create(
            owner=self.teacher,
            name="其他课程",
            code="OTHER-PARTICIPANTS",
            term="2026 秋",
        )
        self.other_class = ClassGroup.objects.create(course=self.other_course, name="外班")
        self.student_a1 = self.create_student("SAVE-A1", self.class_a)
        self.student_a2 = self.create_student("SAVE-A2", self.class_a)
        self.student_b1 = self.create_student("SAVE-B1", self.class_b)
        self.student_other = self.create_student(
            "SAVE-OTHER",
            self.other_class,
            course=self.other_course,
        )
        bank = QuestionBank.objects.create(
            owner=self.teacher,
            course=self.course,
            name="规则保存题库",
        )
        self.experiment = Experiment.objects.create(
            owner=self.teacher,
            created_by=self.teacher,
            course=self.course,
            question_bank=bank,
            name="规则保存实验",
        )

    def create_student(self, number, class_group, *, course=None):
        student = User.objects.create_user(
            username=number,
            student_number=number,
            display_name=number,
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=course or self.course,
            class_group=class_group,
            student=student,
        )
        return student

    def test_class_rule_with_include_and_exclude_materializes_current_preview(self):
        result = save_participant_rules(
            self.experiment,
            [self.class_a],
            [self.student_b1],
            [self.student_a2],
        )

        self.assertEqual(result.preview_count, 2)

        self.assertEqual(
            list(
                self.experiment.participant_class_links.values_list("class_group_id", flat=True)
            ),
            [self.class_a.pk],
        )
        self.assertEqual(
            set(
                self.experiment.participant_override_links.values_list(
                    "student_id",
                    "mode",
                )
            ),
            {
                (self.student_b1.pk, ExperimentParticipantOverride.Mode.INCLUDE),
                (self.student_a2.pk, ExperimentParticipantOverride.Mode.EXCLUDE),
            },
        )
        previews = {
            link.student_id: link.class_snapshot_name
            for link in self.experiment.student_links.all()
        }
        self.assertEqual(
            previews,
            {
                self.student_a1.pk: str(self.class_a),
                self.student_b1.pk: str(self.class_b),
            },
        )

    def test_resaving_class_rule_rebuilds_preview_from_latest_enrollment(self):
        save_participant_rules(self.experiment, [self.class_a], [], [])
        late_student = self.create_student("SAVE-A3", self.class_a)

        save_participant_rules(self.experiment, [self.class_a], [], [])

        self.assertEqual(
            set(self.experiment.student_links.values_list("student_id", flat=True)),
            {self.student_a1.pk, self.student_a2.pk, late_student.pk},
        )

    def test_legacy_course_assignment_service_records_explicit_include_rules(self):
        assign_students(self.experiment, [self.student_a1, self.student_b1])

        self.assertFalse(self.experiment.participant_class_links.exists())
        self.assertEqual(
            set(
                self.experiment.participant_override_links.values_list(
                    "student_id",
                    "mode",
                )
            ),
            {
                (self.student_a1.pk, ExperimentParticipantOverride.Mode.INCLUDE),
                (self.student_b1.pk, ExperimentParticipantOverride.Mode.INCLUDE),
            },
        )

    def test_save_locks_experiment_then_course_then_classes(self):
        from . import services

        lock_order = []
        original_experiment_lock = Experiment.objects.select_for_update
        original_course_lock = services.lock_course
        original_class_lock = ClassGroup.objects.select_for_update

        def lock_experiment(*args, **kwargs):
            lock_order.append("experiment")
            return original_experiment_lock(*args, **kwargs)

        def lock_course(*args, **kwargs):
            lock_order.append("course")
            return original_course_lock(*args, **kwargs)

        def lock_classes(*args, **kwargs):
            lock_order.append("class")
            return original_class_lock(*args, **kwargs)

        with (
            patch.object(
                Experiment.objects,
                "select_for_update",
                side_effect=lock_experiment,
            ),
            patch("apps.experiments.services.lock_course", side_effect=lock_course),
            patch.object(
                ClassGroup.objects,
                "select_for_update",
                side_effect=lock_classes,
            ),
        ):
            save_participant_rules(self.experiment, [self.class_a], [], [])

        self.assertEqual(lock_order[:3], ["experiment", "course", "class"])

    def test_assign_students_locks_experiment_then_course_then_classes(self):
        from . import services

        lock_order = []
        original_experiment_lock = Experiment.objects.select_for_update
        original_course_lock = services.lock_course
        original_class_lock = ClassGroup.objects.select_for_update

        def lock_experiment(*args, **kwargs):
            lock_order.append("experiment")
            return original_experiment_lock(*args, **kwargs)

        def lock_course(*args, **kwargs):
            lock_order.append("course")
            return original_course_lock(*args, **kwargs)

        def lock_classes(*args, **kwargs):
            lock_order.append("class")
            return original_class_lock(*args, **kwargs)

        with (
            patch.object(
                Experiment.objects,
                "select_for_update",
                side_effect=lock_experiment,
            ),
            patch("apps.experiments.services.lock_course", side_effect=lock_course),
            patch.object(
                ClassGroup.objects,
                "select_for_update",
                side_effect=lock_classes,
            ),
        ):
            assign_students(self.experiment, [self.student_a1])

        self.assertEqual(lock_order[:3], ["experiment", "course", "class"])

    def test_assign_students_rechecks_a_stale_draft_before_replacing_roster(self):
        save_participant_rules(self.experiment, [self.class_a], [], [])
        expected_preview = set(
            self.experiment.student_links.values_list("student_id", flat=True)
        )
        stale_experiment = Experiment.objects.get(pk=self.experiment.pk)
        Experiment.objects.filter(pk=self.experiment.pk).update(
            status=Experiment.Status.PUBLISHED
        )

        with self.assertRaisesMessage(ValidationError, "发布后"):
            assign_students(stale_experiment, [self.student_b1])

        self.assertEqual(
            set(self.experiment.student_links.values_list("student_id", flat=True)),
            expected_preview,
        )

    def test_invalid_rules_do_not_replace_existing_preview_or_rules(self):
        save_participant_rules(self.experiment, [self.class_a], [], [])
        expected_preview = set(
            self.experiment.student_links.values_list("student_id", flat=True)
        )
        expected_classes = set(
            self.experiment.participant_class_links.values_list("class_group_id", flat=True)
        )

        with self.assertRaisesMessage(ValidationError, "不能同时"):
            save_participant_rules(
                self.experiment,
                [self.class_a],
                [self.student_b1],
                [self.student_b1],
            )

        self.assertEqual(
            set(self.experiment.student_links.values_list("student_id", flat=True)),
            expected_preview,
        )
        self.assertEqual(
            set(
                self.experiment.participant_class_links.values_list("class_group_id", flat=True)
            ),
            expected_classes,
        )

    def test_rejects_other_course_inactive_or_retired_requested_participants(self):
        with self.assertRaisesMessage(ValidationError, "实验所属课程"):
            save_participant_rules(self.experiment, [self.other_class], [], [])

        self.class_a.is_active = False
        self.class_a.save(update_fields=["is_active"])
        with self.assertRaisesMessage(ValidationError, "停用"):
            save_participant_rules(self.experiment, [self.class_a], [], [])

        CourseEnrollment.objects.filter(
            course=self.course,
            student=self.student_b1,
        ).update(is_active=False)
        with self.assertRaisesMessage(ValidationError, "在读学生"):
            save_participant_rules(self.experiment, [], [self.student_b1], [])

    def test_rejects_malicious_ids_and_archived_course_without_mutation(self):
        save_participant_rules(self.experiment, [self.class_a], [], [])
        expected_preview = set(
            self.experiment.student_links.values_list("student_id", flat=True)
        )

        with self.assertRaisesMessage(ValidationError, "无效对象"):
            save_participant_rules(self.experiment, ["not-an-id"], [], [])
        with self.assertRaisesMessage(ValidationError, "已变更"):
            save_participant_rules(self.experiment, [999999], [], [])

        self.course.status = Course.Status.ARCHIVED
        self.course.save(update_fields=["status"])
        with self.assertRaisesMessage(ValidationError, "课程已归档"):
            save_participant_rules(self.experiment, [self.class_a], [], [])

        self.assertEqual(
            set(self.experiment.student_links.values_list("student_id", flat=True)),
            expected_preview,
        )


class ParticipantRulePublishServiceTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="participant-publish-teacher",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="规则发布课程",
            code="PUBLISH-PARTICIPANTS",
            term="2026 秋",
        )
        self.class_a = ClassGroup.objects.create(
            course=self.course,
            academic_year="2026",
            name="发布甲班",
        )
        self.class_b = ClassGroup.objects.create(
            course=self.course,
            academic_year="2026",
            name="发布乙班",
        )
        self.student_a1 = self.create_student("PUBLISH-A1", self.class_a)
        self.student_a2 = self.create_student("PUBLISH-A2", self.class_a)
        self.student_b1 = self.create_student("PUBLISH-B1", self.class_b)
        self.bank = QuestionBank.objects.create(
            owner=self.teacher,
            created_by=self.teacher,
            course=self.course,
            name="规则发布题库",
        )
        self.question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="发布题目",
            correct_answer="答案",
        )
        concept = Concept.objects.create(course=self.course, name="发布 KC")
        QuestionConcept.objects.create(
            question=self.question,
            concept=concept,
            is_primary=True,
        )
        self.experiment = Experiment.objects.create(
            owner=self.teacher,
            created_by=self.teacher,
            course=self.course,
            question_bank=self.bank,
            name="规则发布实验",
        )
        ExperimentQuestion.objects.create(
            experiment=self.experiment,
            question=self.question,
            display_order=1,
        )

    def create_student(self, number, class_group):
        student = User.objects.create_user(
            username=number,
            student_number=number,
            display_name=number,
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=class_group,
            student=student,
        )
        return student

    def test_publish_rebuilds_latest_roster_and_freezes_class_snapshot(self):
        save_participant_rules(
            self.experiment,
            [self.class_a],
            [self.student_b1],
            [self.student_a2],
        )
        late_student = self.create_student("PUBLISH-A3", self.class_a)

        version = publish_experiment(self.experiment, self.teacher)

        published_links = {
            link.student_id: link.class_snapshot_name
            for link in self.experiment.student_links.all()
        }
        self.assertEqual(
            published_links,
            {
                self.student_a1.pk: str(self.class_a),
                self.student_b1.pk: str(self.class_b),
                late_student.pk: str(self.class_a),
            },
        )
        self.assertEqual(version.version_number, 1)
        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.status, Experiment.Status.PUBLISHED)

        CourseEnrollment.objects.filter(
            course=self.course,
            student=self.student_a1,
        ).update(class_group=self.class_b)
        self.experiment.student_links.get(student=self.student_a1).refresh_from_db()
        self.assertEqual(
            self.experiment.student_links.get(student=self.student_a1).class_snapshot_name,
            str(self.class_a),
        )

    def test_publish_locks_experiment_then_course_then_classes(self):
        from . import services

        save_participant_rules(self.experiment, [self.class_a], [], [])
        lock_order = []
        original_experiment_lock = Experiment.objects.select_for_update
        original_course_lock = services.lock_course
        original_class_lock = ClassGroup.objects.select_for_update

        def lock_experiment(*args, **kwargs):
            lock_order.append("experiment")
            return original_experiment_lock(*args, **kwargs)

        def lock_course(*args, **kwargs):
            lock_order.append("course")
            return original_course_lock(*args, **kwargs)

        def lock_classes(*args, **kwargs):
            lock_order.append("class")
            return original_class_lock(*args, **kwargs)

        with (
            patch.object(
                Experiment.objects,
                "select_for_update",
                side_effect=lock_experiment,
            ),
            patch("apps.experiments.services.lock_course", side_effect=lock_course),
            patch.object(
                ClassGroup.objects,
                "select_for_update",
                side_effect=lock_classes,
            ),
        ):
            publish_experiment(self.experiment, self.teacher)

        self.assertEqual(lock_order[:3], ["experiment", "course", "class"])

    def test_publish_rejects_inactive_selected_class_without_creating_a_version(self):
        save_participant_rules(self.experiment, [self.class_a], [], [])
        ClassGroup.objects.filter(pk=self.class_a.pk).update(is_active=False)

        with self.assertRaisesMessage(ValidationError, "停用"):
            publish_experiment(self.experiment, self.teacher)

        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.status, Experiment.Status.DRAFT)
        self.assertFalse(self.experiment.versions.exists())

    def test_publish_rejects_a_direct_course_roster_without_participant_rules(self):
        ExperimentStudent.objects.create(
            experiment=self.experiment,
            student=self.student_a1,
            class_snapshot_name=str(self.class_a),
        )

        with self.assertRaisesMessage(ValidationError, "请先配置参与班级与学生"):
            publish_experiment(self.experiment, self.teacher)

        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.status, Experiment.Status.DRAFT)
        self.assertFalse(self.experiment.versions.exists())

    def test_publish_rejects_a_retired_direct_course_roster_without_rules(self):
        ExperimentStudent.objects.create(
            experiment=self.experiment,
            student=self.student_a1,
            class_snapshot_name=str(self.class_a),
        )
        CourseEnrollment.objects.filter(
            course=self.course,
            student=self.student_a1,
        ).update(is_active=False)

        with self.assertRaisesMessage(ValidationError, "请先配置参与班级与学生"):
            publish_experiment(self.experiment, self.teacher)

        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.status, Experiment.Status.DRAFT)
        self.assertFalse(self.experiment.versions.exists())

    def test_publish_rejects_an_inactive_class_direct_course_roster_without_rules(self):
        ExperimentStudent.objects.create(
            experiment=self.experiment,
            student=self.student_a1,
            class_snapshot_name=str(self.class_a),
        )
        ClassGroup.objects.filter(pk=self.class_a.pk).update(is_active=False)

        with self.assertRaisesMessage(ValidationError, "请先配置参与班级与学生"):
            publish_experiment(self.experiment, self.teacher)

        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.status, Experiment.Status.DRAFT)
        self.assertFalse(self.experiment.versions.exists())

    def test_publish_rejects_a_selected_class_moved_out_of_course(self):
        save_participant_rules(self.experiment, [self.class_a], [], [])
        other_course = Course.objects.create(
            owner=self.teacher,
            name="移出参与规则课程",
            code="MOVED-PARTICIPANTS",
            term="2026 秋",
        )
        ClassGroup.objects.filter(pk=self.class_a.pk).update(course=other_course)

        with self.assertRaisesMessage(ValidationError, "移出当前课程"):
            publish_experiment(self.experiment, self.teacher)

        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.status, Experiment.Status.DRAFT)
        self.assertFalse(self.experiment.versions.exists())

    def test_publish_rejects_a_retired_explicit_include_without_creating_a_version(self):
        save_participant_rules(self.experiment, [], [self.student_b1], [])
        CourseEnrollment.objects.filter(
            course=self.course,
            student=self.student_b1,
        ).update(is_active=False)

        with self.assertRaisesMessage(ValidationError, "退出"):
            publish_experiment(self.experiment, self.teacher)

        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.status, Experiment.Status.DRAFT)
        self.assertFalse(self.experiment.versions.exists())

    def test_publish_rejects_an_empty_final_roster_without_creating_a_version(self):
        save_participant_rules(self.experiment, [self.class_a], [], [])
        CourseEnrollment.objects.filter(
            course=self.course,
            class_group=self.class_a,
        ).update(is_active=False)

        with self.assertRaisesMessage(ValidationError, "至少需要"):
            publish_experiment(self.experiment, self.teacher)

        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.status, Experiment.Status.DRAFT)
        self.assertFalse(self.experiment.versions.exists())

    def test_publish_ignores_a_retired_exclusion_without_adding_a_student(self):
        save_participant_rules(self.experiment, [self.class_a], [], [self.student_a2])
        CourseEnrollment.objects.filter(
            course=self.course,
            student=self.student_a2,
        ).update(is_active=False)

        publish_experiment(self.experiment, self.teacher)

        self.assertEqual(
            set(self.experiment.student_links.values_list("student_id", flat=True)),
            {self.student_a1.pk},
        )

    def test_legacy_publish_revalidates_the_current_student_class(self):
        legacy_class = ClassGroup.objects.create(name="历史停用班", is_active=False)
        legacy_student = User.objects.create_user(
            username="LEGACY-INACTIVE-CLASS",
            student_number="LEGACY-INACTIVE-CLASS",
            role=User.Role.STUDENT,
            class_group=legacy_class,
        )
        legacy_bank = QuestionBank.objects.create(
            owner=self.teacher,
            name="历史规则题库",
        )
        legacy_question = Question.objects.create(
            bank=legacy_bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="历史规则题目",
            correct_answer="答案",
        )
        legacy_concept = Concept.objects.create(
            owner=self.teacher,
            scope=Concept.Scope.PRIVATE,
            name="历史规则 KC",
            normalized_name="历史规则 kc",
        )
        QuestionConcept.objects.create(
            question=legacy_question,
            concept=legacy_concept,
            is_primary=True,
        )
        legacy_experiment = Experiment.objects.create(
            owner=self.teacher,
            created_by=self.teacher,
            question_bank=legacy_bank,
            name="历史规则实验",
        )
        ExperimentQuestion.objects.create(
            experiment=legacy_experiment,
            question=legacy_question,
            display_order=1,
        )
        ExperimentStudent.objects.create(
            experiment=legacy_experiment,
            student=legacy_student,
            class_snapshot_name=str(legacy_class),
        )

        with self.assertRaisesMessage(ValidationError, "停用班级"):
            publish_experiment(legacy_experiment, self.teacher)

        legacy_experiment.refresh_from_db()
        self.assertEqual(legacy_experiment.status, Experiment.Status.DRAFT)
        self.assertFalse(legacy_experiment.versions.exists())

    def test_legacy_publish_locks_experiment_then_class_then_participants(self):
        legacy_class = ClassGroup.objects.create(name="历史可用班")
        legacy_student = User.objects.create_user(
            username="LEGACY-LOCK-ORDER",
            student_number="LEGACY-LOCK-ORDER",
            role=User.Role.STUDENT,
            class_group=legacy_class,
        )
        legacy_bank = QuestionBank.objects.create(
            owner=self.teacher,
            name="历史锁序题库",
        )
        legacy_question = Question.objects.create(
            bank=legacy_bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="历史锁序题目",
            correct_answer="答案",
        )
        legacy_concept = Concept.objects.create(
            owner=self.teacher,
            scope=Concept.Scope.PRIVATE,
            name="历史锁序 KC",
            normalized_name="历史锁序 kc",
        )
        QuestionConcept.objects.create(
            question=legacy_question,
            concept=legacy_concept,
            is_primary=True,
        )
        legacy_experiment = Experiment.objects.create(
            owner=self.teacher,
            created_by=self.teacher,
            question_bank=legacy_bank,
            name="历史锁序实验",
        )
        ExperimentQuestion.objects.create(
            experiment=legacy_experiment,
            question=legacy_question,
            display_order=1,
        )
        ExperimentStudent.objects.create(
            experiment=legacy_experiment,
            student=legacy_student,
            class_snapshot_name=str(legacy_class),
        )

        lock_order = []
        original_experiment_lock = Experiment.objects.select_for_update
        original_class_lock = ClassGroup.objects.select_for_update
        original_participant_lock = ExperimentStudent.objects.select_for_update

        def lock_experiment(*args, **kwargs):
            lock_order.append("experiment")
            return original_experiment_lock(*args, **kwargs)

        def lock_classes(*args, **kwargs):
            lock_order.append("class")
            return original_class_lock(*args, **kwargs)

        def lock_participants(*args, **kwargs):
            lock_order.append("participant")
            return original_participant_lock(*args, **kwargs)

        with (
            patch.object(
                Experiment.objects,
                "select_for_update",
                side_effect=lock_experiment,
            ),
            patch.object(
                ClassGroup.objects,
                "select_for_update",
                side_effect=lock_classes,
            ),
            patch.object(
                ExperimentStudent.objects,
                "select_for_update",
                side_effect=lock_participants,
            ),
        ):
            publish_experiment(legacy_experiment, self.teacher)

        self.assertEqual(lock_order[:3], ["experiment", "class", "participant"])


class ParticipantRuleViewTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="participant-view-teacher",
            password="x",
            role=User.Role.TEACHER,
        )
        self.other_teacher = User.objects.create_user(
            username="participant-view-other-teacher",
            password="x",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="参与者界面课程",
            code="VIEW-PARTICIPANTS",
            term="2026 秋",
        )
        self.class_a = ClassGroup.objects.create(course=self.course, name="界面甲班")
        self.class_b = ClassGroup.objects.create(course=self.course, name="界面乙班")
        self.inactive_class = ClassGroup.objects.create(
            course=self.course,
            name="停用界面班",
            is_active=False,
        )
        self.student_a1 = self.create_student("VIEW-A1", self.class_a)
        self.student_a2 = self.create_student("VIEW-A2", self.class_a)
        self.student_b1 = self.create_student("VIEW-B1", self.class_b)
        self.inactive_student = self.create_student("VIEW-INACTIVE", self.inactive_class)
        bank = QuestionBank.objects.create(
            owner=self.teacher,
            created_by=self.teacher,
            course=self.course,
            name="参与者界面题库",
        )
        self.experiment = Experiment.objects.create(
            owner=self.teacher,
            created_by=self.teacher,
            course=self.course,
            question_bank=bank,
            name="参与者界面实验",
        )
        self.detail_url = reverse(
            "courses:experiment_detail",
            args=[self.course.pk, self.experiment.pk],
        )
        self.assign_url = reverse(
            "courses:experiment_assign",
            args=[self.course.pk, self.experiment.pk],
        )
        self.client.force_login(self.teacher)

    def create_student(self, number, class_group):
        student = User.objects.create_user(
            username=number,
            student_number=number,
            display_name=number,
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=class_group,
            student=student,
        )
        return student

    def test_draft_participant_dom_is_native_disclosure_and_no_js_form_complete(self):
        response = self.client.get(self.detail_url)

        self.assertContains(response, "参与班级与学生")
        self.assertContains(response, "当前预览")
        self.assertContains(response, "<details", html=False)
        self.assertContains(response, "<summary", html=False)
        for field_name in ("classes", "includes", "excludes"):
            self.assertContains(response, f'name="{field_name}"', html=False)
        self.assertContains(response, 'data-participant-class', html=False)
        self.assertContains(response, 'aria-live="polite"', html=False)
        self.assertContains(response, self.class_a.name)
        self.assertContains(response, self.class_b.name)
        self.assertNotContains(response, self.inactive_class.name)
        self.assertNotContains(response, self.inactive_student.student_number)

    def test_assignment_post_saves_rule_counts_without_identity_audit_payload(self):
        response = self.client.post(
            self.assign_url,
            {
                "classes": [self.class_a.pk],
                "includes": [self.student_b1.pk],
                "excludes": [self.student_a2.pk],
            },
        )

        self.assertRedirects(response, self.detail_url)
        self.assertEqual(
            set(self.experiment.student_links.values_list("student_id", flat=True)),
            {self.student_a1.pk, self.student_b1.pk},
        )
        audit = AuditLog.objects.get(
            action="experiment.assign_students",
            object_id=str(self.experiment.pk),
        )
        self.assertEqual(
            audit.details,
            {
                "class_count": 1,
                "include_count": 1,
                "exclude_count": 1,
                "preview_count": 2,
            },
        )
        self.assertNotIn(str(self.student_a1.pk), str(audit.details))
        self.assertNotIn(str(self.student_b1.pk), str(audit.details))

    def test_assignment_audit_uses_the_service_materialized_preview_count(self):
        with patch(
            "apps.experiments.course_views.save_participant_rules",
            return_value=SimpleNamespace(preview_count=37),
        ):
            response = self.client.post(
                self.assign_url,
                {"classes": [self.class_a.pk]},
            )

        self.assertRedirects(response, self.detail_url)
        audit = AuditLog.objects.get(
            action="experiment.assign_students",
            object_id=str(self.experiment.pk),
        )
        self.assertEqual(audit.details["preview_count"], 37)

    def test_assignment_rejects_other_course_ids_and_requires_post_csrf_and_access(self):
        other_course = Course.objects.create(
            owner=self.other_teacher,
            name="越界参与课程",
            code="CROSS-PARTICIPANTS",
            term="2026 秋",
        )
        other_class = ClassGroup.objects.create(course=other_course, name="越界班")

        response = self.client.post(self.assign_url, {"classes": [other_class.pk]})
        self.assertRedirects(response, self.detail_url)
        self.assertFalse(self.experiment.participant_class_links.exists())
        self.assertEqual(self.client.get(self.assign_url).status_code, 405)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.teacher)
        self.assertEqual(
            csrf_client.post(self.assign_url, {"classes": [self.class_a.pk]}).status_code,
            403,
        )

        self.client.force_login(self.other_teacher)
        self.assertEqual(self.client.get(self.detail_url).status_code, 404)

    def test_published_experiment_hides_and_locks_participant_entry(self):
        self.experiment.status = Experiment.Status.PUBLISHED
        self.experiment.save(update_fields=["status"])

        response = self.client.get(self.detail_url)

        self.assertNotContains(response, 'name="classes"', html=False)
        self.assertNotContains(response, 'name="includes"', html=False)
        self.assertNotContains(response, 'name="excludes"', html=False)
        self.assertContains(response, "实验已锁定")


class ParticipantRuleStaticContractTests(TestCase):
    def test_participant_controls_use_native_disclosure_and_progressive_enhancement(self):
        template = (ROOT / "templates" / "teacher" / "course_experiment_detail.html").read_text(
            encoding="utf-8"
        )
        script = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        stylesheet = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")

        for token in (
            "<details",
            "<summary",
            'name="classes"',
            'name="includes"',
            'name="excludes"',
            "data-participant-class",
            "data-participant-class-input",
            "data-participant-include",
            "data-participant-exclude",
            "<noscript>",
        ):
            self.assertIn(token, template)
        for token in (
            "data-participant-rules",
            "data-participant-class",
            "data-participant-class-input",
            "data-participant-include",
            "data-participant-exclude",
            "input.disabled = selected",
            "input.disabled = !selected",
        ):
            self.assertIn(token, script)
        for selector in (
            ".participant-rule-fieldset",
            ".participant-class-disclosure",
            ".participant-student-row",
            ".participant-student-actions",
        ):
            self.assertIn(selector, stylesheet)


class ParticipantRuleDemoSeedTests(TestCase):
    def test_seed_demo_configures_participant_rules_before_publishing_course_experiment(self):
        call_command("seed_demo", stdout=StringIO())

        experiment = Experiment.objects.get(
            course__code="KT101",
            name="演示交互数据采集",
        )
        self.assertEqual(experiment.status, Experiment.Status.PUBLISHED)
        self.assertEqual(experiment.student_links.count(), 3)
        self.assertEqual(
            experiment.participant_override_links.filter(
                mode=ExperimentParticipantOverride.Mode.INCLUDE
            ).count(),
            3,
        )

    def test_seed_demo_locks_experiment_course_and_class_before_enrollment_write(self):
        lock_order = []
        original_experiment_lock = Experiment.objects.select_for_update
        original_course_lock = Course.objects.select_for_update
        original_class_lock = ClassGroup.objects.select_for_update
        original_enrollment_write = CourseEnrollment.objects.update_or_create

        def lock_experiment(*args, **kwargs):
            lock_order.append("experiment")
            return original_experiment_lock(*args, **kwargs)

        def lock_course(*args, **kwargs):
            lock_order.append("course")
            return original_course_lock(*args, **kwargs)

        def lock_class(*args, **kwargs):
            lock_order.append("class")
            return original_class_lock(*args, **kwargs)

        def write_enrollment(*args, **kwargs):
            lock_order.append("enrollment")
            return original_enrollment_write(*args, **kwargs)

        with (
            patch.object(
                Experiment.objects,
                "select_for_update",
                side_effect=lock_experiment,
            ),
            patch.object(Course.objects, "select_for_update", side_effect=lock_course),
            patch.object(ClassGroup.objects, "select_for_update", side_effect=lock_class),
            patch.object(
                CourseEnrollment.objects,
                "update_or_create",
                side_effect=write_enrollment,
            ),
        ):
            call_command("seed_demo", stdout=StringIO())

        enrollment_index = lock_order.index("enrollment")
        self.assertEqual(
            lock_order[enrollment_index - 3 : enrollment_index + 1],
            ["experiment", "course", "class", "enrollment"],
        )
