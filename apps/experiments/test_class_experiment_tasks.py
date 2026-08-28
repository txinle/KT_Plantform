from datetime import timedelta

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept
from apps.question_bank.services.course_question_identity import (
    allocate_course_question_identity,
)

from .models import (
    ClassExperimentTask,
    Experiment,
    ExperimentClassSnapshot,
    ExperimentQuestion,
    ExperimentVersion,
)
from .services import delete_experiment, publish_experiment, save_participant_rules


class ClassExperimentTaskTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="class-task-teacher",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="班级实验任务课程",
            code="TASK101",
            term="2026 秋",
        )
        self.class_a = ClassGroup.objects.create(
            course=self.course,
            name="任务 A 班",
            academic_year="2023",
        )
        self.class_b = ClassGroup.objects.create(
            course=self.course,
            name="任务 B 班",
            academic_year="2023",
        )
        self.student_a = self._create_student("TASK-A-001", self.class_a)
        self.student_b = self._create_student("TASK-B-001", self.class_b)
        self.bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="班级实验任务题库",
        )
        question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="任务快照题",
            correct_answer="答案",
        )
        allocate_course_question_identity(self.course, question)
        concept = Concept.objects.create(course=self.course, name="任务 KC")
        QuestionConcept.objects.create(
            question=question,
            concept=concept,
            is_primary=True,
        )
        self.experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=self.bank,
            name="班级实验任务实验",
        )
        ExperimentQuestion.objects.create(
            experiment=self.experiment,
            question=question,
            display_order=1,
        )

    def _create_student(self, student_number, class_group):
        student = User.objects.create_user(
            username=student_number,
            student_number=student_number,
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=class_group,
            student=student,
        )
        return student

    def _version_and_snapshot(self, *, version_number, class_group):
        version = ExperimentVersion.objects.create(
            experiment=self.experiment,
            version_number=version_number,
            published_by=self.teacher,
        )
        snapshot = ExperimentClassSnapshot.objects.create(
            version=version,
            source_class_group=class_group,
            name_snapshot=class_group.name,
            academic_year_snapshot=class_group.academic_year,
            capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
        )
        return version, snapshot

    def _create_task_for_publication(self, *, class_group, version, snapshot):
        return ClassExperimentTask._create_for_publication(
            class_group=class_group,
            experiment=self.experiment,
            release_snapshot=version,
            class_snapshot=snapshot,
            published_at=version.published_at,
        )

    def test_same_class_and_experiment_can_have_only_one_permanent_task(self):
        first_version, first_snapshot = self._version_and_snapshot(
            version_number=1,
            class_group=self.class_a,
        )
        self._create_task_for_publication(
            class_group=self.class_a,
            version=first_version,
            snapshot=first_snapshot,
        )
        second_version, second_snapshot = self._version_and_snapshot(
            version_number=2,
            class_group=self.class_a,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._create_task_for_publication(
                    class_group=self.class_a,
                    version=second_version,
                    snapshot=second_snapshot,
                )

    def test_same_experiment_can_have_tasks_for_different_classes(self):
        version, snapshot_a = self._version_and_snapshot(
            version_number=1,
            class_group=self.class_a,
        )
        snapshot_b = ExperimentClassSnapshot.objects.create(
            version=version,
            source_class_group=self.class_b,
            name_snapshot=self.class_b.name,
            academic_year_snapshot=self.class_b.academic_year,
            capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
        )

        self._create_task_for_publication(
            class_group=self.class_a,
            version=version,
            snapshot=snapshot_a,
        )
        self._create_task_for_publication(
            class_group=self.class_b,
            version=version,
            snapshot=snapshot_b,
        )

        self.assertEqual(
            ClassExperimentTask.objects.filter(experiment=self.experiment).count(),
            2,
        )

    def test_alignment_validation_rejects_wrong_course_snapshot_version_and_timestamp(self):
        version, snapshot = self._version_and_snapshot(
            version_number=1,
            class_group=self.class_a,
        )
        other_course = Course.objects.create(
            owner=self.teacher,
            name="其他班级实验任务课程",
            code="TASK102",
            term="2026 秋",
        )
        other_class = ClassGroup.objects.create(
            course=other_course,
            name="其他任务班",
        )
        other_experiment = Experiment.objects.create(
            course=other_course,
            created_by=self.teacher,
            question_bank=QuestionBank.objects.create(
                course=other_course,
                created_by=self.teacher,
                name="其他班级实验任务题库",
            ),
            name="其他班级实验任务实验",
        )
        other_version = ExperimentVersion.objects.create(
            experiment=other_experiment,
            version_number=1,
            published_by=self.teacher,
        )
        other_snapshot = ExperimentClassSnapshot.objects.create(
            version=other_version,
            source_class_group=other_class,
            name_snapshot=other_class.name,
            academic_year_snapshot=other_class.academic_year,
            capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
        )

        invalid_tasks = {
            "class_group": ClassExperimentTask(
                class_group=other_class,
                experiment=self.experiment,
                release_snapshot=version,
                class_snapshot=snapshot,
                published_at=version.published_at,
            ),
            "release_snapshot": ClassExperimentTask(
                class_group=self.class_a,
                experiment=self.experiment,
                release_snapshot=other_version,
                class_snapshot=other_snapshot,
                published_at=other_version.published_at,
            ),
            "class_snapshot": ClassExperimentTask(
                class_group=self.class_a,
                experiment=self.experiment,
                release_snapshot=version,
                class_snapshot=other_snapshot,
                published_at=version.published_at,
            ),
            "published_at": ClassExperimentTask(
                class_group=self.class_a,
                experiment=self.experiment,
                release_snapshot=version,
                class_snapshot=snapshot,
                published_at=version.published_at + timedelta(seconds=1),
            ),
        }

        for field_name, task in invalid_tasks.items():
            with self.subTest(field_name=field_name):
                with self.assertRaises(ValidationError) as error:
                    task.full_clean()
                self.assertIn(field_name, error.exception.message_dict)

    def test_tasks_reject_direct_creation_updates_and_deletion(self):
        version, snapshot = self._version_and_snapshot(
            version_number=1,
            class_group=self.class_a,
        )
        task = self._create_task_for_publication(
            class_group=self.class_a,
            version=version,
            snapshot=snapshot,
        )

        with self.assertRaises(ValidationError):
            ClassExperimentTask.objects.create(
                class_group=self.class_a,
                experiment=self.experiment,
                release_snapshot=version,
                class_snapshot=snapshot,
                published_at=version.published_at,
            )
        with self.assertRaises(ValidationError):
            task.save()
        with self.assertRaises(ValidationError):
            ClassExperimentTask.objects.filter(pk=task.pk).update(
                published_at=task.published_at
            )
        with self.assertRaises(ValidationError):
            ClassExperimentTask.objects.bulk_update([task], ["published_at"])
        with self.assertRaises(ValidationError):
            ClassExperimentTask.objects.bulk_create(
                [
                    ClassExperimentTask(
                        class_group=self.class_a,
                        experiment=self.experiment,
                        release_snapshot=version,
                        class_snapshot=snapshot,
                        published_at=version.published_at,
                    )
                ]
            )
        with self.assertRaises(ValidationError):
            task.delete()
        with self.assertRaises(ValidationError):
            ClassExperimentTask.objects.filter(pk=task.pk).delete()

    def test_admin_disallows_class_experiment_task_writes(self):
        model_admin = admin.site._registry[ClassExperimentTask]
        request = RequestFactory().get("/admin/")

        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))

    def test_publish_creates_one_aligned_task_for_each_published_class(self):
        save_participant_rules(
            self.experiment,
            [self.class_a, self.class_b],
            [],
            [],
        )

        version = publish_experiment(self.experiment, self.teacher)

        tasks = ClassExperimentTask.objects.filter(experiment=self.experiment).order_by(
            "class_group_id"
        )
        self.assertEqual(tasks.count(), 2)
        self.assertEqual(
            set(tasks.values_list("class_group_id", flat=True)),
            {self.class_a.pk, self.class_b.pk},
        )
        for task in tasks:
            self.assertEqual(task.release_snapshot_id, version.pk)
            self.assertEqual(task.class_snapshot.version_id, version.pk)
            self.assertEqual(task.class_snapshot.source_class_group_id, task.class_group_id)
            self.assertEqual(task.published_at, version.published_at)

    def test_business_experiment_deletion_is_rejected_when_a_formal_task_exists(self):
        save_participant_rules(self.experiment, [self.class_a], [], [])
        publish_experiment(self.experiment, self.teacher)

        with self.assertRaisesMessage(ValidationError, "正式班级实验任务"):
            delete_experiment(self.experiment)

        self.assertTrue(Experiment.objects.filter(pk=self.experiment.pk).exists())
        self.assertEqual(
            ClassExperimentTask.objects.filter(experiment=self.experiment).count(),
            1,
        )

    def test_delete_view_returns_to_experiment_when_a_formal_task_exists(self):
        save_participant_rules(self.experiment, [self.class_a], [], [])
        publish_experiment(self.experiment, self.teacher)
        self.client.force_login(self.teacher)

        response = self.client.post(
            reverse(
                "courses:experiment_delete",
                args=[self.course.pk, self.experiment.pk],
            ),
            {"confirmation": "delete"},
        )

        self.assertRedirects(
            response,
            reverse(
                "courses:experiment_detail",
                args=[self.course.pk, self.experiment.pk],
            ),
        )
        self.assertTrue(Experiment.objects.filter(pk=self.experiment.pk).exists())

    def test_publish_rolls_back_all_new_rows_when_a_class_task_conflicts(self):
        save_participant_rules(self.experiment, [self.class_a], [], [])
        existing_version, existing_snapshot = self._version_and_snapshot(
            version_number=1,
            class_group=self.class_a,
        )
        self._create_task_for_publication(
            class_group=self.class_a,
            version=existing_version,
            snapshot=existing_snapshot,
        )

        with self.assertRaises(IntegrityError):
            publish_experiment(self.experiment, self.teacher)

        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.status, Experiment.Status.DRAFT)
        self.assertEqual(
            ExperimentVersion.objects.filter(experiment=self.experiment).count(),
            1,
        )
        self.assertEqual(
            ExperimentClassSnapshot.objects.filter(version=existing_version).count(),
            1,
        )
        self.assertEqual(
            ClassExperimentTask.objects.filter(experiment=self.experiment).count(),
            1,
        )
