from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.analytics.export_fields import FIELD_LABELS
from apps.assessment.models import ExperimentSession
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept
from .forms import ExperimentForm, StudentAssignmentForm
from .models import Experiment, ExperimentQuestion, ExperimentStudent, ExperimentVersion, QuestionSnapshot
from .services import assign_students, publish_experiment, sync_experiment_questions

class PublishingTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="teacher", role=User.Role.TEACHER)
        self.student = User.objects.create_user(username="S1", role=User.Role.STUDENT, student_number="S1")
        self.bank = QuestionBank.objects.create(owner=self.teacher, name="bank")
        self.question = Question.objects.create(bank=self.bank, sequence=1, question_type=Question.Type.SHORT, text="KT?", correct_answer="知识追踪")
        self.experiment = Experiment.objects.create(owner=self.teacher, question_bank=self.bank, name="exp")
        ExperimentQuestion.objects.create(experiment=self.experiment, question=self.question, display_order=1)
        ExperimentStudent.objects.create(experiment=self.experiment, student=self.student)

    def test_publish_rejects_question_without_primary_kc(self):
        with self.assertRaises(ValidationError):
            publish_experiment(self.experiment, self.teacher)

    def test_publish_copies_immutable_snapshot(self):
        concept = Concept.objects.create(owner=self.teacher, scope=Concept.Scope.PRIVATE, name="追踪", normalized_name="追踪")
        QuestionConcept.objects.create(question=self.question, concept=concept, is_primary=True)
        version = publish_experiment(self.experiment, self.teacher)
        snapshot = version.question_snapshots.get()
        self.question.text = "changed"
        self.question.save()
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.text, "KT?")
        self.assertEqual(snapshot.primary_kc, "追踪")


class ArchivedQuestionBankExperimentServiceTests(TestCase):
    archived_bank_message = "题库已归档，不能同步或发布实验。"

    def setUp(self):
        self.teacher = User.objects.create_user(username="archived-bank-experiment-teacher", role=User.Role.TEACHER)
        self.student = User.objects.create_user(
            username="ARCHIVED-BANK-STUDENT",
            student_number="ARCHIVED-BANK-STUDENT",
            role=User.Role.STUDENT,
        )
        self.bank = QuestionBank.objects.create(owner=self.teacher, name="实验归档题库")
        self.question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="实验题目",
            correct_answer="实验答案",
        )
        concept = Concept.objects.create(
            owner=self.teacher,
            scope=Concept.Scope.PRIVATE,
            name="实验 KC",
            normalized_name="实验 kc",
        )
        QuestionConcept.objects.create(question=self.question, concept=concept, is_primary=True)
        self.experiment = Experiment.objects.create(
            owner=self.teacher,
            question_bank=self.bank,
            name="归档绕过实验",
        )
        ExperimentQuestion.objects.create(
            experiment=self.experiment,
            question=self.question,
            display_order=1,
        )
        ExperimentStudent.objects.create(experiment=self.experiment, student=self.student)

    def stale_experiment_after_archiving_its_bank(self):
        stale_experiment = Experiment.objects.select_related("question_bank").get(pk=self.experiment.pk)
        self.assertEqual(stale_experiment.question_bank.visibility, QuestionBank.Visibility.PRIVATE)
        QuestionBank.objects.filter(pk=self.bank.pk).update(
            visibility=QuestionBank.Visibility.ARCHIVED
        )
        return stale_experiment

    def test_sync_rejects_an_archived_bank_from_a_stale_experiment_without_replacing_links(self):
        stale_experiment = self.stale_experiment_after_archiving_its_bank()
        before_links = list(
            self.experiment.question_links.values_list("question_id", "display_order")
        )

        with self.assertRaisesMessage(ValidationError, self.archived_bank_message):
            sync_experiment_questions(stale_experiment)

        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.status, Experiment.Status.DRAFT)
        self.assertEqual(
            list(self.experiment.question_links.values_list("question_id", "display_order")),
            before_links,
        )
        self.assertEqual(ExperimentVersion.objects.filter(experiment=self.experiment).count(), 0)
        self.assertEqual(QuestionSnapshot.objects.count(), 0)

    def test_publish_rejects_an_archived_bank_from_a_stale_experiment_without_creating_snapshots(self):
        stale_experiment = self.stale_experiment_after_archiving_its_bank()

        with self.assertRaisesMessage(ValidationError, self.archived_bank_message):
            publish_experiment(stale_experiment, self.teacher)

        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.status, Experiment.Status.DRAFT)
        self.assertEqual(ExperimentVersion.objects.filter(experiment=self.experiment).count(), 0)
        self.assertEqual(QuestionSnapshot.objects.count(), 0)


class ExperimentFormArchivedBankRaceTests(TestCase):
    archived_bank_message = "题库已归档，不能同步或发布实验。"

    def setUp(self):
        self.teacher = User.objects.create_user(
            username="experiment-form-race-teacher",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="事务实验课程",
            code="TX-EXP",
            term="2026 秋",
        )
        self.course_bank = QuestionBank.objects.create(
            owner=self.teacher,
            course=self.course,
            name="课程原题库",
        )
        self.course_target_bank = QuestionBank.objects.create(
            owner=self.teacher,
            course=self.course,
            name="课程待归档题库",
        )
        self.legacy_bank = QuestionBank.objects.create(owner=self.teacher, name="旧版原题库")
        self.legacy_target_bank = QuestionBank.objects.create(
            owner=self.teacher,
            name="旧版待归档题库",
        )
        self.course_experiment = Experiment.objects.create(
            owner=self.teacher,
            created_by=self.teacher,
            course=self.course,
            question_bank=self.course_bank,
            name="课程既有草稿",
        )
        self.legacy_experiment = Experiment.objects.create(
            owner=self.teacher,
            question_bank=self.legacy_bank,
            name="旧版既有草稿",
        )
        self.client.force_login(self.teacher)

    def payload(self, bank, name):
        return {
            "name": name,
            "description": "",
            "question_bank": bank.pk,
            "ordering_mode": Experiment.Ordering.FIXED,
            "feedback_mode": Experiment.Feedback.NONE,
            "time_mode": Experiment.TimeMode.NONE,
            "total_time_limit_minutes": "",
            "per_question_time_limit_seconds": "",
            "resume_mode": Experiment.ResumeMode.RESUMABLE,
        }

    def post_after_archiving_the_validated_bank(self, url, bank, payload):
        original_is_valid = ExperimentForm.is_valid

        def archive_after_validation(form):
            is_valid = original_is_valid(form)
            if is_valid:
                QuestionBank.objects.filter(pk=bank.pk).update(
                    visibility=QuestionBank.Visibility.ARCHIVED
                )
            return is_valid

        original_raise_request_exception = self.client.raise_request_exception
        self.client.raise_request_exception = False
        try:
            with patch.object(ExperimentForm, "is_valid", new=archive_after_validation):
                return self.client.post(url, payload)
        finally:
            self.client.raise_request_exception = original_raise_request_exception

    def assert_archived_bank_error(self, response, bank):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.archived_bank_message)
        bank.refresh_from_db()
        self.assertEqual(bank.visibility, QuestionBank.Visibility.ARCHIVED)

    def test_course_create_rolls_back_when_the_validated_bank_is_archived_before_save(self):
        response = self.post_after_archiving_the_validated_bank(
            reverse("courses:experiment_create", args=[self.course.pk]),
            self.course_target_bank,
            self.payload(self.course_target_bank, "不应创建的课程草稿"),
        )

        self.assert_archived_bank_error(response, self.course_target_bank)
        self.assertFalse(Experiment.objects.filter(name="不应创建的课程草稿").exists())

    def test_course_edit_keeps_the_existing_bank_when_the_target_is_archived_before_save(self):
        response = self.post_after_archiving_the_validated_bank(
            reverse("courses:experiment_edit", args=[self.course.pk, self.course_experiment.pk]),
            self.course_target_bank,
            self.payload(self.course_target_bank, "不应换绑的课程草稿"),
        )

        self.assert_archived_bank_error(response, self.course_target_bank)
        self.course_experiment.refresh_from_db()
        self.assertEqual(self.course_experiment.question_bank_id, self.course_bank.pk)

    def test_legacy_create_rolls_back_when_the_validated_bank_is_archived_before_save(self):
        response = self.post_after_archiving_the_validated_bank(
            reverse("experiments:create"),
            self.legacy_target_bank,
            self.payload(self.legacy_target_bank, "不应创建的旧版草稿"),
        )

        self.assert_archived_bank_error(response, self.legacy_target_bank)
        self.assertFalse(Experiment.objects.filter(name="不应创建的旧版草稿").exists())

    def test_legacy_edit_keeps_the_existing_bank_when_the_target_is_archived_before_save(self):
        response = self.post_after_archiving_the_validated_bank(
            reverse("experiments:edit", args=[self.legacy_experiment.pk]),
            self.legacy_target_bank,
            self.payload(self.legacy_target_bank, "不应换绑的旧版草稿"),
        )

        self.assert_archived_bank_error(response, self.legacy_target_bank)
        self.legacy_experiment.refresh_from_db()
        self.assertEqual(self.legacy_experiment.question_bank_id, self.legacy_bank.pk)


class ExperimentFormPublishedRaceTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="experiment-publish-race-teacher",
            role=User.Role.TEACHER,
        )
        self.student = User.objects.create_user(
            username="EXPERIMENT-PUBLISH-RACE-STUDENT",
            student_number="EXPERIMENT-PUBLISH-RACE-STUDENT",
            role=User.Role.STUDENT,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="发布竞争课程",
            code="PUB-RACE",
            term="2026 秋",
        )
        self.course_bank = QuestionBank.objects.create(
            owner=self.teacher,
            course=self.course,
            name="课程发布题库",
        )
        self.legacy_bank = QuestionBank.objects.create(
            owner=self.teacher,
            name="旧版发布题库",
        )
        self.course_question = Question.objects.create(
            bank=self.course_bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="课程发布题目",
            correct_answer="课程答案",
        )
        self.legacy_question = Question.objects.create(
            bank=self.legacy_bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="旧版发布题目",
            correct_answer="旧版答案",
        )
        course_concept = Concept.objects.create(course=self.course, name="课程发布 KC")
        legacy_concept = Concept.objects.create(
            owner=self.teacher,
            scope=Concept.Scope.PRIVATE,
            name="旧版发布 KC",
            normalized_name="旧版发布 kc",
        )
        QuestionConcept.objects.create(
            question=self.course_question,
            concept=course_concept,
            is_primary=True,
        )
        QuestionConcept.objects.create(
            question=self.legacy_question,
            concept=legacy_concept,
            is_primary=True,
        )
        self.course_experiment = Experiment.objects.create(
            owner=self.teacher,
            created_by=self.teacher,
            course=self.course,
            question_bank=self.course_bank,
            name="课程发布草稿",
        )
        self.legacy_experiment = Experiment.objects.create(
            owner=self.teacher,
            question_bank=self.legacy_bank,
            name="旧版发布草稿",
        )
        for experiment, question in (
            (self.course_experiment, self.course_question),
            (self.legacy_experiment, self.legacy_question),
        ):
            ExperimentQuestion.objects.create(
                experiment=experiment,
                question=question,
                display_order=1,
            )
            ExperimentStudent.objects.create(experiment=experiment, student=self.student)
        self.client.force_login(self.teacher)

    def payload(self, bank, name):
        return {
            "name": name,
            "description": "",
            "question_bank": bank.pk,
            "ordering_mode": Experiment.Ordering.FIXED,
            "feedback_mode": Experiment.Feedback.NONE,
            "time_mode": Experiment.TimeMode.NONE,
            "total_time_limit_minutes": "",
            "per_question_time_limit_seconds": "",
            "resume_mode": Experiment.ResumeMode.RESUMABLE,
        }

    def assert_published_experiment_is_unchanged(self, response, experiment, original_name):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "实验发布后不能修改配置")
        experiment.refresh_from_db()
        self.assertEqual(experiment.status, Experiment.Status.PUBLISHED)
        self.assertEqual(experiment.name, original_name)
        self.assertEqual(ExperimentVersion.objects.filter(experiment=experiment).count(), 1)
        self.assertEqual(
            QuestionSnapshot.objects.filter(version__experiment=experiment).count(),
            1,
        )

    def test_course_edit_rejects_a_stale_draft_after_it_is_published(self):
        from . import course_views

        original_loader = course_views._experiment

        def load_then_publish(course, pk):
            stale_experiment = original_loader(course, pk)
            publish_experiment(stale_experiment, self.teacher)
            return stale_experiment

        response = None
        with patch("apps.experiments.course_views._experiment", side_effect=load_then_publish):
            response = self.client.post(
                reverse(
                    "courses:experiment_edit",
                    args=[self.course.pk, self.course_experiment.pk],
                ),
                self.payload(self.course_bank, "不应覆盖已发布课程实验"),
            )

        self.assert_published_experiment_is_unchanged(
            response,
            self.course_experiment,
            "课程发布草稿",
        )

    def test_legacy_edit_rejects_a_stale_draft_after_it_is_published(self):
        from . import views

        original_loader = views._owned

        def load_then_publish(request, pk):
            stale_experiment = original_loader(request, pk)
            publish_experiment(stale_experiment, self.teacher)
            return stale_experiment

        response = None
        with patch("apps.experiments.views._owned", side_effect=load_then_publish):
            response = self.client.post(
                reverse("experiments:edit", args=[self.legacy_experiment.pk]),
                self.payload(self.legacy_bank, "不应覆盖已发布旧版实验"),
            )

        self.assert_published_experiment_is_unchanged(
            response,
            self.legacy_experiment,
            "旧版发布草稿",
        )


class LegacyExperimentInactiveClassAssignmentTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="legacy-teacher",
            password="x",
            role=User.Role.TEACHER,
        )
        self.active_class = ClassGroup.objects.create(
            owner=self.teacher,
            academic_year="2026",
            name="可用班级",
        )
        self.inactive_class = ClassGroup.objects.create(
            owner=self.teacher,
            academic_year="2026",
            name="停用班级",
            is_active=False,
        )
        self.active_student = User.objects.create_user(
            username="ACTIVE-STUDENT",
            student_number="ACTIVE-STUDENT",
            class_group=self.active_class,
            role=User.Role.STUDENT,
        )
        self.inactive_student = User.objects.create_user(
            username="INACTIVE-STUDENT",
            student_number="INACTIVE-STUDENT",
            class_group=self.inactive_class,
            role=User.Role.STUDENT,
        )
        bank = QuestionBank.objects.create(owner=self.teacher, name="legacy-bank")
        self.experiment = Experiment.objects.create(
            owner=self.teacher,
            question_bank=bank,
            name="legacy-experiment",
        )
        self.client.force_login(self.teacher)

    def test_legacy_assignment_form_hides_students_from_inactive_classes(self):
        form = StudentAssignmentForm(owner=self.teacher)

        self.assertIn(self.active_student, form.fields["students"].queryset)
        self.assertNotIn(self.inactive_student, form.fields["students"].queryset)

    def test_legacy_assignment_direct_post_rejects_an_inactive_class_student(self):
        response = self.client.post(
            reverse("experiments:assign", args=[self.experiment.pk]),
            {"students": [self.inactive_student.pk]},
        )

        self.assertRedirects(response, reverse("experiments:detail", args=[self.experiment.pk]))
        self.assertFalse(
            ExperimentStudent.objects.filter(
                experiment=self.experiment,
                student=self.inactive_student,
            ).exists()
        )

    def test_legacy_assignment_service_rejects_an_inactive_class_student_without_replacing_assignments(self):
        ExperimentStudent.objects.create(
            experiment=self.experiment,
            student=self.active_student,
            class_snapshot_name=str(self.active_class),
        )

        with self.assertRaisesMessage(ValidationError, "停用班级"):
            assign_students(self.experiment, [self.inactive_student])

        self.assertEqual(
            list(self.experiment.student_links.values_list("student_id", flat=True)),
            [self.active_student.pk],
        )

    def test_legacy_assignment_rejects_a_class_deactivated_after_the_student_was_read(self):
        ExperimentStudent.objects.create(
            experiment=self.experiment,
            student=self.active_student,
            class_snapshot_name=str(self.active_class),
        )
        stale_student = User.objects.select_related("class_group").get(pk=self.active_student.pk)
        ClassGroup.objects.filter(pk=self.active_class.pk).update(is_active=False)

        with self.assertRaisesMessage(ValidationError, "停用班级"):
            assign_students(self.experiment, [stale_student])

        self.assertEqual(
            list(self.experiment.student_links.values_list("student_id", flat=True)),
            [self.active_student.pk],
        )


class CourseExperimentInactiveClassAssignmentTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="course-experiment-teacher",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="实验课程",
            code="EXP-LOCK",
            term="2026春",
        )
        self.class_group = ClassGroup.objects.create(course=self.course, name="实验班")
        self.student = User.objects.create_user(
            username="COURSE-STALE-STUDENT",
            student_number="COURSE-STALE-STUDENT",
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_group,
            student=self.student,
        )
        bank = QuestionBank.objects.create(owner=self.teacher, course=self.course, name="课程实验题库")
        self.experiment = Experiment.objects.create(
            owner=self.teacher,
            created_by=self.teacher,
            course=self.course,
            question_bank=bank,
            name="课程实验",
        )
        ExperimentStudent.objects.create(
            experiment=self.experiment,
            student=self.student,
            class_snapshot_name=str(self.class_group),
        )

    def test_course_assignment_rejects_a_class_deactivated_after_the_student_was_read(self):
        stale_student = User.objects.get(pk=self.student.pk)
        ClassGroup.objects.filter(pk=self.class_group.pk).update(is_active=False)

        with self.assertRaisesMessage(ValidationError, "可用班级"):
            assign_students(self.experiment, [stale_student])

        self.assertEqual(
            list(self.experiment.student_links.values_list("student_id", flat=True)),
            [self.student.pk],
        )


class ExperimentResultStatusFilterTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="teacher", password="x", role=User.Role.TEACHER)
        self.other_teacher = User.objects.create_user(username="other", password="x", role=User.Role.TEACHER)
        bank = QuestionBank.objects.create(owner=self.teacher, name="bank")
        self.experiment = Experiment.objects.create(owner=self.teacher, question_bank=bank, name="状态实验")
        old_version = ExperimentVersion.objects.create(
            experiment=self.experiment,
            version_number=1,
            published_by=self.teacher,
        )
        self.latest_version = ExperimentVersion.objects.create(
            experiment=self.experiment,
            version_number=2,
            published_by=self.teacher,
        )
        self.students = {}
        for index, (status, label) in enumerate(
            [
                (ExperimentSession.Status.IN_PROGRESS, "进行中学生"),
                (ExperimentSession.Status.COMPLETED, "已完成学生"),
                (ExperimentSession.Status.EXPIRED, "已超时学生"),
                (ExperimentSession.Status.INVALIDATED, "已作废学生"),
            ],
            start=1,
        ):
            student = User.objects.create_user(
                username=f"S{index}",
                student_number=f"S{index}",
                display_name=label,
                role=User.Role.STUDENT,
            )
            ExperimentSession.objects.create(version=self.latest_version, student=student, status=status)
            self.students[status] = label
        old_student = User.objects.create_user(
            username="OLD",
            student_number="OLD",
            display_name="旧版本学生",
            role=User.Role.STUDENT,
        )
        ExperimentSession.objects.create(version=old_version, student=old_student)
        self.client.force_login(self.teacher)
        self.url = reverse("experiments:results", args=[self.experiment.pk])

    def test_default_lists_only_latest_version_and_all_statuses(self):
        response = self.client.get(self.url)

        for label in self.students.values():
            self.assertContains(response, label)
        self.assertNotContains(response, "旧版本学生")
        self.assertContains(response, "4 条")

    def test_filters_each_session_status_and_preserves_selection(self):
        for status, expected_label in self.students.items():
            with self.subTest(status=status):
                response = self.client.get(self.url, {"status": status})
                self.assertContains(response, expected_label)
                for other_label in set(self.students.values()) - {expected_label}:
                    self.assertNotContains(response, other_label)
                self.assertContains(response, f'value="{status}" selected')
                self.assertContains(response, "1 条")

    def test_invalid_status_falls_back_to_all(self):
        response = self.client.get(self.url, {"status": "not-a-status"})

        for label in self.students.values():
            self.assertContains(response, label)
        self.assertNotContains(response, 'value="not-a-status"')

    def test_other_teacher_receives_404(self):
        self.client.force_login(self.other_teacher)

        self.assertEqual(self.client.get(self.url, {"status": "completed"}).status_code, 404)

    def test_results_page_makes_identified_exports_primary_and_keeps_legacy_links(self):
        response = self.client.get(self.url)
        html = response.content.decode("utf-8")

        identified_url = reverse("analytics:identified_export", args=[self.experiment.pk])
        navigation_url = reverse("analytics:identified_navigation_export", args=[self.experiment.pk])
        self.assertContains(response, f'action="{identified_url}"')
        self.assertContains(response, f'action="{navigation_url}"')
        self.assertContains(response, "实名可选字段 Excel")
        self.assertContains(response, "兼容导出")
        self.assertEqual(len(FIELD_LABELS), 34)
        self.assertEqual(html.count('type="checkbox" name="fields"'), 34)
        for field in FIELD_LABELS:
            self.assertContains(response, f'name="fields" value="{field}" checked')
        self.assertContains(response, reverse("analytics:kt_export", args=[self.experiment.pk]))
        self.assertContains(response, reverse("analytics:navigation_export", args=[self.experiment.pk]))
        self.assertContains(response, reverse("analytics:identity_export", args=[self.experiment.pk]))
