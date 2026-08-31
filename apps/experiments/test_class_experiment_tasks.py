from datetime import timedelta
from unittest.mock import patch

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import Case, F, Value, When
from django.test import RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

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
from .services import (
    _class_experiment_task_publication_scope,
    _create_class_experiment_tasks,
    _publish_legacy_experiment as publish_experiment,
    delete_experiment,
    save_participant_rules,
)


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
        return self._insert_historical_task(
            class_group=class_group,
            version=version,
            snapshot=snapshot,
        )

    def _insert_historical_task(self, *, class_group, version, snapshot):
        """Seed a pre-contract row without using the current model write API."""
        table = connection.ops.quote_name(ClassExperimentTask._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {table}
                    (class_group_id, experiment_id, release_snapshot_id,
                     class_snapshot_id, published_at, terminal_reason,
                     provenance, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    class_group.pk,
                    self.experiment.pk,
                    version.pk,
                    snapshot.pk.hex,
                    version.published_at,
                    "",
                    "native",
                    timezone.now(),
                ],
            )
        return ClassExperimentTask.objects.get(
            class_group=class_group,
            experiment=self.experiment,
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

    def _publish_class_a_task(self):
        save_participant_rules(self.experiment, [self.class_a], [], [])
        version = publish_experiment(self.experiment, self.teacher)
        task = ClassExperimentTask.objects.get(
            experiment=self.experiment,
            class_group=self.class_a,
        )
        return version, task.class_snapshot, task

    def _assert_selected_empty_class_is_frozen(self, version):
        snapshots = ExperimentClassSnapshot.objects.filter(version=version)
        self.assertEqual(
            set(snapshots.values_list("source_class_group_id", flat=True)),
            {self.class_a.pk, self.class_b.pk},
        )
        snapshot_b = snapshots.get(source_class_group=self.class_b)
        self.assertEqual(snapshot_b.participant_snapshots.count(), 0)
        self.assertEqual(
            ClassExperimentTask.objects.filter(
                experiment=self.experiment,
                class_group=self.class_b,
                release_snapshot=version,
                class_snapshot=snapshot_b,
            ).count(),
            1,
        )

    def test_publish_freezes_an_empty_selected_class(self):
        CourseEnrollment.objects.filter(
            course=self.course,
            student=self.student_b,
        ).update(is_active=False)
        save_participant_rules(
            self.experiment,
            [self.class_a, self.class_b],
            [],
            [],
        )

        version = publish_experiment(self.experiment, self.teacher)

        self._assert_selected_empty_class_is_frozen(version)

    def test_publish_freezes_a_selected_class_with_all_students_excluded(self):
        save_participant_rules(
            self.experiment,
            [self.class_a, self.class_b],
            [],
            [self.student_b],
        )

        version = publish_experiment(self.experiment, self.teacher)

        self._assert_selected_empty_class_is_frozen(version)

    def test_base_manager_rejects_task_update(self):
        _version, _snapshot, task = self._publish_class_a_task()

        with self.assertRaises(ValidationError):
            ClassExperimentTask._base_manager.filter(pk=task.pk).update(
                published_at=task.published_at
            )

    def test_base_manager_rejects_task_bulk_create(self):
        version, _snapshot_a, _task = self._publish_class_a_task()
        snapshot_b = ExperimentClassSnapshot.objects.create(
            version=version,
            source_class_group=self.class_b,
            name_snapshot=self.class_b.name,
            academic_year_snapshot=self.class_b.academic_year,
            capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
        )
        candidate = ClassExperimentTask(
            class_group=self.class_b,
            experiment=self.experiment,
            release_snapshot=version,
            class_snapshot=snapshot_b,
            published_at=version.published_at,
        )

        with self.assertRaises(ValidationError):
            ClassExperimentTask._base_manager.bulk_create([candidate])

    def test_base_manager_rejects_task_delete(self):
        _version, _snapshot, task = self._publish_class_a_task()

        with self.assertRaises(ValidationError):
            ClassExperimentTask._base_manager.filter(pk=task.pk).delete()

    def test_task_model_exposes_no_publication_creation_api(self):
        self.assertFalse(hasattr(ClassExperimentTask, "_publication_creation_context"))
        self.assertFalse(hasattr(ClassExperimentTask, "_create_for_publication"))

    def test_task_creator_rejects_normal_service_calls_without_publish_scope(self):
        version, _snapshot_a = self._version_and_snapshot(
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

        with self.assertRaises(ValidationError):
            _create_class_experiment_tasks(
                self.experiment,
                version,
                {self.class_b.pk: snapshot_b},
            )

    def test_task_backed_release_and_class_snapshots_cannot_be_mutated(self):
        version, snapshot, task = self._publish_class_a_task()
        original_published_at = version.published_at
        original_class_group_id = snapshot.source_class_group_id

        version.published_at = version.published_at + timedelta(seconds=1)
        with self.assertRaises(ValidationError):
            version.save(update_fields=["published_at"])
        with self.assertRaises(ValidationError):
            ExperimentVersion.objects.filter(pk=version.pk).update(
                published_at=version.published_at
            )
        with self.assertRaises(ValidationError):
            ExperimentVersion._base_manager.filter(pk=version.pk).update(
                published_at=version.published_at
            )
        with transaction.atomic():
            with self.assertRaises(ValidationError):
                ExperimentVersion._base_manager.bulk_update(
                    [version], ["published_at"]
                )
        with self.assertRaises(ValidationError):
            version.delete()
        with self.assertRaises(ValidationError):
            ExperimentVersion.objects.filter(pk=version.pk).delete()
        with self.assertRaises(ValidationError):
            ExperimentVersion._base_manager.filter(pk=version.pk).delete()

        snapshot.source_class_group = self.class_b
        with self.assertRaises(ValidationError):
            snapshot.save(update_fields=["source_class_group"])
        with self.assertRaises(ValidationError):
            ExperimentClassSnapshot.objects.filter(pk=snapshot.pk).update(
                source_class_group=self.class_b
            )
        with self.assertRaises(ValidationError):
            ExperimentClassSnapshot._base_manager.filter(pk=snapshot.pk).update(
                source_class_group=self.class_b
            )
        with transaction.atomic():
            with self.assertRaises(ValidationError):
                ExperimentClassSnapshot._base_manager.bulk_update(
                    [snapshot], ["source_class_group"]
                )
        with self.assertRaises(ValidationError):
            snapshot.delete()
        with self.assertRaises(ValidationError):
            ExperimentClassSnapshot.objects.filter(pk=snapshot.pk).delete()
        with self.assertRaises(ValidationError):
            ExperimentClassSnapshot._base_manager.filter(pk=snapshot.pk).delete()

        version.refresh_from_db()
        snapshot.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(version.published_at, original_published_at)
        self.assertEqual(snapshot.source_class_group_id, original_class_group_id)
        self.assertEqual(task.release_snapshot_id, version.pk)
        self.assertEqual(task.class_snapshot_id, snapshot.pk)
        self.assertEqual(task.published_at, version.published_at)

    def test_admin_disallows_version_writes_after_a_task_is_created(self):
        version, _snapshot, _task = self._publish_class_a_task()
        model_admin = admin.site._registry[ExperimentVersion]
        request = RequestFactory().get("/admin/")

        self.assertFalse(model_admin.has_change_permission(request, version))
        self.assertFalse(model_admin.has_delete_permission(request, version))

    def _other_course(self):
        return Course.objects.create(
            owner=self.teacher,
            name="其他任务归属课程",
            code="TASK103",
            term="2026 秋",
        )

    def test_task_backed_experiment_rejects_instance_course_change(self):
        _version, _snapshot, task = self._publish_class_a_task()
        other_course = self._other_course()
        self.experiment.course = other_course

        with self.assertRaisesMessage(ValidationError, "实验所属课程不可变更"):
            self.experiment.save(update_fields=["course"])

        task.refresh_from_db()
        self.assertEqual(task.experiment.course_id, task.class_group.course_id)

    def test_task_backed_experiment_rejects_default_manager_course_change(self):
        _version, _snapshot, task = self._publish_class_a_task()
        other_course = self._other_course()

        with self.assertRaisesMessage(ValidationError, "实验所属课程不可变更"):
            Experiment.objects.filter(pk=self.experiment.pk).update(course=other_course)

        task.refresh_from_db()
        self.assertEqual(task.experiment.course_id, task.class_group.course_id)

    def test_task_backed_experiment_rejects_base_manager_course_change(self):
        _version, _snapshot, task = self._publish_class_a_task()
        other_course = self._other_course()

        with self.assertRaisesMessage(ValidationError, "实验所属课程不可变更"):
            Experiment._base_manager.filter(pk=self.experiment.pk).update(
                course_id=other_course.pk
            )

        task.refresh_from_db()
        self.assertEqual(task.experiment.course_id, task.class_group.course_id)

    def test_task_backed_class_group_rejects_instance_course_change(self):
        _version, _snapshot, task = self._publish_class_a_task()
        other_course = self._other_course()
        self.class_a.course = other_course

        with self.assertRaisesMessage(ValidationError, "班级所属课程不可变更"):
            self.class_a.save(update_fields=["course"])

        task.refresh_from_db()
        self.assertEqual(task.experiment.course_id, task.class_group.course_id)

    def test_task_backed_class_group_rejects_default_manager_course_change(self):
        _version, _snapshot, task = self._publish_class_a_task()
        other_course = self._other_course()

        with self.assertRaisesMessage(ValidationError, "班级所属课程不可变更"):
            ClassGroup.objects.filter(pk=self.class_a.pk).update(course=other_course)

        task.refresh_from_db()
        self.assertEqual(task.experiment.course_id, task.class_group.course_id)

    def test_task_backed_class_group_rejects_base_manager_course_change(self):
        _version, _snapshot, task = self._publish_class_a_task()
        other_course = self._other_course()

        with self.assertRaisesMessage(ValidationError, "班级所属课程不可变更"):
            ClassGroup._base_manager.filter(pk=self.class_a.pk).update(
                course_id=other_course.pk
            )

        task.refresh_from_db()
        self.assertEqual(task.experiment.course_id, task.class_group.course_id)

    def test_task_backed_parents_reject_course_bulk_updates(self):
        _version, _snapshot, task = self._publish_class_a_task()
        other_course = self._other_course()
        cases = (
            ("experiment default manager", Experiment.objects, Experiment, task.experiment_id),
            ("experiment base manager", Experiment._base_manager, Experiment, task.experiment_id),
            ("class group default manager", ClassGroup.objects, ClassGroup, task.class_group_id),
            ("class group base manager", ClassGroup._base_manager, ClassGroup, task.class_group_id),
        )

        for label, manager, model, primary_key in cases:
            for target_label, target_course in (
                ("different course", other_course),
                ("same course", self.course),
            ):
                with self.subTest(label=label, target=target_label):
                    parent = model.objects.get(pk=primary_key)
                    parent.course = target_course

                    with transaction.atomic():
                        with self.assertRaises(ValidationError):
                            manager.bulk_update([parent], ["course"])

                    experiment_course_id = Experiment.objects.get(
                        pk=task.experiment_id
                    ).course_id
                    class_course_id = ClassGroup.objects.get(
                        pk=task.class_group_id
                    ).course_id
                    self.assertEqual(experiment_course_id, class_course_id)

    def test_task_backed_parents_reject_none_course_bulk_updates(self):
        _version, _snapshot, task = self._publish_class_a_task()
        cases = (
            ("experiment default manager", Experiment.objects, Experiment, task.experiment_id),
            ("experiment base manager", Experiment._base_manager, Experiment, task.experiment_id),
            ("class group default manager", ClassGroup.objects, ClassGroup, task.class_group_id),
            ("class group base manager", ClassGroup._base_manager, ClassGroup, task.class_group_id),
        )

        for label, manager, model, primary_key in cases:
            with self.subTest(label=label):
                parent = model.objects.get(pk=primary_key)
                parent.course = None

                with transaction.atomic():
                    with self.assertRaises(ValidationError):
                        manager.bulk_update([parent], ["course"])

                self.assertEqual(
                    Experiment.objects.get(pk=task.experiment_id).course_id,
                    ClassGroup.objects.get(pk=task.class_group_id).course_id,
                )

    def test_task_backed_parents_reject_null_expression_course_updates(self):
        _version, _snapshot, task = self._publish_class_a_task()
        cases = (
            ("experiment default manager", Experiment.objects, Experiment, task.experiment_id),
            ("experiment base manager", Experiment._base_manager, Experiment, task.experiment_id),
            ("class group default manager", ClassGroup.objects, ClassGroup, task.class_group_id),
            ("class group base manager", ClassGroup._base_manager, ClassGroup, task.class_group_id),
        )

        for label, manager, model, primary_key in cases:
            with self.subTest(label=label):
                null_course = Case(
                    When(pk=primary_key, then=Value(None)),
                    output_field=model._meta.get_field("course"),
                )

                with transaction.atomic():
                    with self.assertRaises(ValidationError):
                        manager.filter(pk=primary_key).update(course=null_course)

                self.assertEqual(
                    Experiment.objects.get(pk=task.experiment_id).course_id,
                    ClassGroup.objects.get(pk=task.class_group_id).course_id,
                )

    def test_task_backed_versions_and_snapshots_reject_conflict_bulk_create(self):
        version, snapshot, _task = self._publish_class_a_task()
        original_configuration = dict(version.configuration)
        original_name = snapshot.name_snapshot
        cases = (
            (
                "version default manager",
                ExperimentVersion.objects,
                ExperimentVersion(
                    experiment=self.experiment,
                    version_number=version.version_number,
                    configuration={"tampered": True},
                    published_by=self.teacher,
                ),
                ["configuration"],
                ["experiment", "version_number"],
            ),
            (
                "version base manager",
                ExperimentVersion._base_manager,
                ExperimentVersion(
                    experiment=self.experiment,
                    version_number=version.version_number,
                    configuration={"tampered": True},
                    published_by=self.teacher,
                ),
                ["configuration"],
                ["experiment", "version_number"],
            ),
            (
                "class snapshot default manager",
                ExperimentClassSnapshot.objects,
                ExperimentClassSnapshot(
                    class_snapshot_id=snapshot.pk,
                    version=version,
                    source_class_group=self.class_a,
                    name_snapshot="被篡改的班级快照",
                    academic_year_snapshot=self.class_a.academic_year,
                    capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
                ),
                ["name_snapshot"],
                ["class_snapshot_id"],
            ),
            (
                "class snapshot base manager",
                ExperimentClassSnapshot._base_manager,
                ExperimentClassSnapshot(
                    class_snapshot_id=snapshot.pk,
                    version=version,
                    source_class_group=self.class_a,
                    name_snapshot="被篡改的班级快照",
                    academic_year_snapshot=self.class_a.academic_year,
                    capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
                ),
                ["name_snapshot"],
                ["class_snapshot_id"],
            ),
        )

        for label, manager, candidate, update_fields, unique_fields in cases:
            with self.subTest(label=label):
                with transaction.atomic():
                    with self.assertRaises(ValidationError):
                        manager.bulk_create(
                            [candidate],
                            update_conflicts=True,
                            update_fields=update_fields,
                            unique_fields=unique_fields,
                        )

                version.refresh_from_db()
                snapshot.refresh_from_db()
                self.assertEqual(version.configuration, original_configuration)
                self.assertEqual(snapshot.name_snapshot, original_name)

    def test_task_backed_parents_reject_course_conflict_bulk_create(self):
        _version, _snapshot, task = self._publish_class_a_task()
        other_course = self._other_course()
        cases = (
            (
                "experiment default manager",
                Experiment.objects,
                Experiment(
                    pk=self.experiment.pk,
                    course=other_course,
                    created_by=self.teacher,
                    owner=self.teacher,
                    question_bank=self.bank,
                    name=self.experiment.name,
                ),
            ),
            (
                "experiment base manager",
                Experiment._base_manager,
                Experiment(
                    pk=self.experiment.pk,
                    course=other_course,
                    created_by=self.teacher,
                    owner=self.teacher,
                    question_bank=self.bank,
                    name=self.experiment.name,
                ),
            ),
            (
                "class group default manager",
                ClassGroup.objects,
                ClassGroup(
                    pk=self.class_a.pk,
                    course=other_course,
                    name=self.class_a.name,
                    academic_year=self.class_a.academic_year,
                ),
            ),
            (
                "class group base manager",
                ClassGroup._base_manager,
                ClassGroup(
                    pk=self.class_a.pk,
                    course=other_course,
                    name=self.class_a.name,
                    academic_year=self.class_a.academic_year,
                ),
            ),
        )

        for label, manager, candidate in cases:
            with self.subTest(label=label):
                with transaction.atomic():
                    with self.assertRaises(ValidationError):
                        manager.bulk_create(
                            [candidate],
                            update_conflicts=True,
                            update_fields=["course"],
                            unique_fields=["pk"],
                        )

                self.assertEqual(
                    Experiment.objects.get(pk=task.experiment_id).course_id,
                    ClassGroup.objects.get(pk=task.class_group_id).course_id,
                )

    def test_draft_experiment_upsert_allows_description_update(self):
        candidate = Experiment.objects.get(pk=self.experiment.pk)
        candidate.description = "允许的草稿说明更新"

        Experiment.objects.bulk_create(
            [candidate],
            update_conflicts=True,
            update_fields=["description"],
            unique_fields=["pk"],
        )

        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.description, "允许的草稿说明更新")

    def test_non_task_class_group_upsert_allows_is_active_update(self):
        candidate = ClassGroup.objects.get(pk=self.class_b.pk)
        candidate.is_active = False

        ClassGroup.objects.bulk_create(
            [candidate],
            update_conflicts=True,
            update_fields=["is_active"],
            unique_fields=["pk"],
        )

        self.class_b.refresh_from_db()
        self.assertFalse(self.class_b.is_active)

    def test_task_backed_class_group_upsert_allows_is_active_update(self):
        self._publish_class_a_task()
        candidate = ClassGroup.objects.get(pk=self.class_a.pk)
        candidate.is_active = False

        ClassGroup.objects.bulk_create(
            [candidate],
            update_conflicts=True,
            update_fields=["is_active"],
            unique_fields=["pk"],
        )

        self.class_a.refresh_from_db()
        self.assertFalse(self.class_a.is_active)

    def test_course_queryset_update_locks_its_original_scope(self):
        other_course = self._other_course()
        cases = (
            ("experiment", Experiment.objects.filter(pk=self.experiment.pk), self.experiment.pk),
            ("class group", ClassGroup.objects.filter(pk=self.class_a.pk), self.class_a.pk),
        )

        for label, queryset, primary_key in cases:
            with self.subTest(label=label):
                queryset_class = type(queryset)
                original_select_for_update = queryset_class.select_for_update
                with patch.object(
                    queryset_class,
                    "select_for_update",
                    autospec=True,
                    side_effect=original_select_for_update,
                ) as select_for_update:
                    updated = queryset.update(course=other_course)

                self.assertEqual(updated, 1)
                select_for_update.assert_called_once()
                locked_queryset = select_for_update.call_args.args[0]
                self.assertEqual(
                    list(locked_queryset.values_list("pk", flat=True)),
                    [primary_key],
                )

    def test_course_changing_instance_save_locks_parent_before_task_check(self):
        self._publish_class_a_task()
        other_course = self._other_course()
        cases = (
            ("experiment", self.experiment),
            ("class group", self.class_a),
        )

        for label, parent in cases:
            with self.subTest(label=label):
                parent.course = other_course
                queryset_class = type(parent.__class__.objects.all())
                original_select_for_update = queryset_class.select_for_update
                with patch.object(
                    queryset_class,
                    "select_for_update",
                    autospec=True,
                    side_effect=original_select_for_update,
                ) as select_for_update:
                    with self.assertRaises(ValidationError):
                        parent.save(update_fields=["course"])

                select_for_update.assert_called_once()
                locked_queryset = select_for_update.call_args.args[0]
                self.assertEqual(
                    list(locked_queryset.values_list("pk", flat=True)),
                    [parent.pk],
                )

    def test_default_non_course_parent_save_does_not_lock_course_row(self):
        _version, _snapshot, task = self._publish_class_a_task()
        cases = (
            (
                "experiment",
                Experiment,
                task.experiment_id,
                "description",
                "不应触发课程行锁的说明更新",
            ),
            (
                "class group",
                ClassGroup,
                task.class_group_id,
                "is_active",
                False,
            ),
        )

        for label, model, primary_key, field_name, value in cases:
            with self.subTest(label=label):
                parent = model.objects.get(pk=primary_key)
                setattr(parent, field_name, value)
                queryset_class = type(model.objects.all())
                original_select_for_update = queryset_class.select_for_update
                with patch.object(
                    queryset_class,
                    "select_for_update",
                    autospec=True,
                    side_effect=original_select_for_update,
                ) as select_for_update:
                    parent.save()

                select_for_update.assert_not_called()
                parent.refresh_from_db()
                self.assertEqual(getattr(parent, field_name), value)

    def test_course_queryset_update_uses_write_database_alias(self):
        other_course = self._other_course()
        cases = (
            ("experiment default manager", Experiment.objects, self.experiment.pk),
            ("experiment base manager", Experiment._base_manager, self.experiment.pk),
            ("class group default manager", ClassGroup.objects, self.class_a.pk),
            ("class group base manager", ClassGroup._base_manager, self.class_a.pk),
        )

        for label, manager, primary_key in cases:
            with self.subTest(label=label):
                with (
                    patch(
                        "django.db.router.db_for_read",
                        side_effect=AssertionError("课程写入不能路由到 reader"),
                    ) as db_for_read,
                    patch("django.db.router.db_for_write", return_value="default") as db_for_write,
                ):
                    updated = manager.filter(pk=primary_key).update(course=other_course)

                self.assertEqual(updated, 1)
                db_for_write.assert_called()
                db_for_read.assert_not_called()

    def test_course_queryset_update_preserves_annotations(self):
        other_course = self._other_course()
        cases = (
            ("experiment", Experiment.objects, self.experiment.pk, Experiment),
            ("class group", ClassGroup.objects, self.class_a.pk, ClassGroup),
        )

        for label, manager, primary_key, model in cases:
            with self.subTest(label=label):
                updated = (
                    manager.filter(pk=primary_key)
                    .annotate(target_course_id=Value(other_course.pk))
                    .update(course_id=F("target_course_id"))
                )

                self.assertEqual(updated, 1)
                self.assertEqual(
                    model.objects.get(pk=primary_key).course_id,
                    other_course.pk,
                )

    def test_successful_course_queryset_update_clears_the_original_cache(self):
        other_course = self._other_course()
        cases = (
            ("experiment", Experiment.objects, self.experiment.pk),
            ("class group", ClassGroup.objects, self.class_a.pk),
        )

        for label, manager, primary_key in cases:
            with self.subTest(label=label):
                queryset = manager.filter(pk=primary_key)
                self.assertEqual(list(queryset)[0].course_id, self.course.pk)

                self.assertEqual(queryset.update(course=other_course), 1)

                self.assertEqual(list(queryset)[0].course_id, other_course.pk)

    def test_empty_course_queryset_update_clears_the_original_cache(self):
        other_course = self._other_course()
        cases = (Experiment.objects, ClassGroup.objects)

        for manager in cases:
            with self.subTest(model=manager.model.__name__):
                queryset = manager.none()
                self.assertEqual(list(queryset), [])

                self.assertEqual(queryset.update(course=other_course), 0)

                self.assertIsNone(queryset._result_cache)

    def test_empty_queryset_course_bulk_update_is_a_noop(self):
        _version, _snapshot, task = self._publish_class_a_task()
        cases = (
            ("experiment", Experiment.objects, task.experiment),
            ("class group", ClassGroup.objects, task.class_group),
        )

        for label, manager, parent in cases:
            with self.subTest(label=label):
                self.assertEqual(manager.none().bulk_update([parent], ["course"]), 0)

    def test_empty_snapshot_bulk_update_accepts_a_generator_and_is_a_noop(self):
        version, snapshot, _task = self._publish_class_a_task()
        cases = (
            ("version default manager", ExperimentVersion.objects, version, ["configuration"]),
            ("version base manager", ExperimentVersion._base_manager, version, ["configuration"]),
            (
                "class snapshot default manager",
                ExperimentClassSnapshot.objects,
                snapshot,
                ["name_snapshot"],
            ),
            (
                "class snapshot base manager",
                ExperimentClassSnapshot._base_manager,
                snapshot,
                ["name_snapshot"],
            ),
        )

        for label, manager, snapshot_model, fields in cases:
            with self.subTest(label=label):
                self.assertEqual(
                    manager.none().bulk_update((item for item in [snapshot_model]), fields),
                    0,
                )

    def test_restricted_querysets_allow_plain_bulk_create(self):
        plain_class = ClassGroup(
            course=self.course,
            name="普通批量创建班",
            academic_year="2024",
        )
        ClassGroup.objects.bulk_create([plain_class])
        plain_experiment = Experiment(
            course=self.course,
            created_by=self.teacher,
            owner=self.teacher,
            question_bank=self.bank,
            name="普通批量创建实验",
        )
        Experiment.objects.bulk_create([plain_experiment])
        plain_version = ExperimentVersion(
            experiment=plain_experiment,
            version_number=1,
            published_by=self.teacher,
        )
        ExperimentVersion.objects.bulk_create([plain_version])
        plain_snapshot = ExperimentClassSnapshot(
            version=plain_version,
            source_class_group=plain_class,
            name_snapshot=plain_class.name,
            academic_year_snapshot=plain_class.academic_year,
            capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
        )
        ExperimentClassSnapshot.objects.bulk_create([plain_snapshot])

        self.assertTrue(ClassGroup.objects.filter(pk=plain_class.pk).exists())
        self.assertTrue(Experiment.objects.filter(pk=plain_experiment.pk).exists())
        self.assertTrue(ExperimentVersion.objects.filter(pk=plain_version.pk).exists())
        self.assertTrue(
            ExperimentClassSnapshot.objects.filter(pk=plain_snapshot.pk).exists()
        )

    def test_task_backed_class_group_allows_non_course_bulk_update(self):
        _version, _snapshot, task = self._publish_class_a_task()
        self.class_a.is_active = False

        updated = ClassGroup.objects.bulk_update([self.class_a], ["is_active"])

        self.assertEqual(updated, 1)
        self.class_a.refresh_from_db()
        self.assertFalse(self.class_a.is_active)
        self.assertEqual(
            Experiment.objects.get(pk=task.experiment_id).course_id,
            ClassGroup.objects.get(pk=task.class_group_id).course_id,
        )

    def test_task_backed_class_group_keeps_non_course_fields_mutable(self):
        self._publish_class_a_task()

        ClassGroup.objects.filter(pk=self.class_a.pk).update(is_active=False)
        self.class_a.refresh_from_db()
        self.assertFalse(self.class_a.is_active)
        self.class_a.is_active = True
        self.class_a.save(update_fields=["is_active"])
        self.class_a.refresh_from_db()
        self.assertTrue(self.class_a.is_active)

    def test_admin_makes_task_backed_parent_courses_read_only(self):
        self._publish_class_a_task()
        request = RequestFactory().get("/admin/")

        experiment_admin = admin.site._registry[Experiment]
        class_group_admin = admin.site._registry[ClassGroup]

        self.assertIn(
            "course",
            experiment_admin.get_readonly_fields(request, self.experiment),
        )
        self.assertIn(
            "course",
            class_group_admin.get_readonly_fields(request, self.class_a),
        )

    def test_legacy_course_publish_view_is_blocked_when_a_task_exists(self):
        save_participant_rules(self.experiment, [self.class_a], [], [])
        existing_version, existing_snapshot = self._version_and_snapshot(
            version_number=1,
            class_group=self.class_a,
        )
        self._insert_historical_task(
            class_group=self.class_a,
            version=existing_version,
            snapshot=existing_snapshot,
        )
        self.client.force_login(self.teacher)

        response = self.client.post(
            reverse(
                "courses:experiment_publish",
                args=[self.course.pk, self.experiment.pk],
            ),
        )

        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "旧版课程草稿", status_code=409)
        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.status, Experiment.Status.DRAFT)
        self.assertEqual(
            ExperimentVersion.objects.filter(experiment=self.experiment).count(),
            1,
        )


class ClassExperimentTaskCreationBoundaryTests(TransactionTestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="task-creation-boundary-teacher",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="任务创建边界课程",
            code="TASK102",
            term="2026 秋",
        )
        self.class_group = ClassGroup.objects.create(
            course=self.course,
            name="创建边界班",
            academic_year="2023",
        )
        bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="任务创建边界题库",
        )
        self.experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=bank,
            name="任务创建边界实验",
        )
        self.version = ExperimentVersion.objects.create(
            experiment=self.experiment,
            version_number=1,
            published_by=self.teacher,
        )
        self.snapshot = ExperimentClassSnapshot.objects.create(
            version=self.version,
            source_class_group=self.class_group,
            name_snapshot=self.class_group.name,
            academic_year_snapshot=self.class_group.academic_year,
            capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
        )

    def _task_kwargs(self):
        return {
            "class_group": self.class_group,
            "experiment": self.experiment,
            "release_snapshot": self.version,
            "class_snapshot": self.snapshot,
            "published_at": self.version.published_at,
        }

    def test_direct_task_creation_fails_without_an_atomic_block(self):
        self.assertFalse(connection.in_atomic_block)

        with self.assertRaises(ValidationError):
            ClassExperimentTask(**self._task_kwargs()).save(force_insert=True)
        with self.assertRaises(ValidationError):
            ClassExperimentTask.objects.create(**self._task_kwargs())

    def test_direct_task_creation_fails_inside_an_unscoped_atomic_block(self):
        with transaction.atomic():
            self.assertTrue(connection.in_atomic_block)
            with self.assertRaises(ValidationError):
                ClassExperimentTask(**self._task_kwargs()).save(force_insert=True)
            with self.assertRaises(ValidationError):
                ClassExperimentTask.objects.create(**self._task_kwargs())

    def test_task_creation_requires_atomic_even_with_publication_scope(self):
        self.assertFalse(connection.in_atomic_block)

        with self.assertRaisesMessage(ValidationError, "发布事务"):
            with _class_experiment_task_publication_scope():
                ClassExperimentTask(**self._task_kwargs()).save(force_insert=True)
