from unittest.mock import patch

from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.forms import ClassGroupUpdateForm
from apps.accounts.models import Course, CourseEnrollment, ClassGroup, User
from apps.audit.models import AuditLog
from apps.experiments.models import Experiment, ExperimentSamplingKC, ExperimentStudent
from apps.experiments.publication_services import (
    commit_publish_plan,
    generate_preview,
    get_or_create_publish_plan,
    save_plan_selection,
)
from apps.experiments.services import assign_students, publish_experiment, sync_experiment_questions

from .models import Concept, ImportPreviewBatch, Question, QuestionBank, QuestionConcept, QuestionOption
from .forms import QuestionBankForm
from .services.import_service import copy_shared_bank


class ArchivedCourseBankCreationRaceTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="archived-course-bank-create-teacher",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            name="待归档目标课程",
            code="BANK-RACE",
            term="2026 秋",
            owner=self.teacher,
        )
        self.source = QuestionBank.objects.create(
            owner=self.teacher,
            name="共享复制源",
            visibility=QuestionBank.Visibility.SHARED,
        )
        Question.objects.create(
            bank=self.source,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="不应复制的源题",
            correct_answer="答案",
        )
        self.client.force_login(self.teacher)

    def bank_payload(self, name):
        return {
            "name": name,
            "description": "",
            "visibility": QuestionBank.Visibility.PRIVATE,
        }

    def assert_no_target_copy(self, before_question_count):
        self.assertFalse(QuestionBank.objects.filter(course=self.course).exists())
        self.assertEqual(Question.objects.count(), before_question_count)

    def post_after_archiving_the_loaded_source(self, url, patch_target):
        from django.shortcuts import get_object_or_404

        def load_then_archive(*args, **kwargs):
            source = get_object_or_404(*args, **kwargs)
            if isinstance(source, QuestionBank):
                QuestionBank.objects.filter(pk=source.pk).update(
                    visibility=QuestionBank.Visibility.ARCHIVED
                )
            return source

        original_raise_request_exception = self.client.raise_request_exception
        self.client.raise_request_exception = False
        try:
            with patch(patch_target, side_effect=load_then_archive):
                return self.client.post(url)
        finally:
            self.client.raise_request_exception = original_raise_request_exception

    def assert_stale_source_copy_is_rejected(self, response, expected_location):
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], expected_location)
        self.assertEqual(
            [str(message) for message in get_messages(response.wsgi_request)],
            ["只能复制已共享题库"],
        )
        self.assert_no_target_copy(1)

    def test_course_bank_create_rejects_a_stale_course_archived_before_save(self):
        stale_course = Course.objects.get(pk=self.course.pk)
        Course.objects.filter(pk=self.course.pk).update(status=Course.Status.ARCHIVED)
        before_question_count = Question.objects.count()

        with patch(
            "apps.question_bank.course_views.get_accessible_course",
            return_value=stale_course,
        ):
            response = self.client.post(
                reverse("courses:banks", args=[self.course.pk]),
                self.bank_payload("不应创建的课程题库"),
        )

        self.assertEqual(response.status_code, 403)
        self.assert_no_target_copy(before_question_count)

    def test_legacy_bank_create_rejects_a_course_archived_after_target_selection(self):
        original_is_valid = QuestionBankForm.is_valid
        before_question_count = Question.objects.count()

        def archive_after_validation(form):
            is_valid = original_is_valid(form)
            if is_valid:
                Course.objects.filter(pk=self.course.pk).update(status=Course.Status.ARCHIVED)
            return is_valid

        with patch.object(QuestionBankForm, "is_valid", new=archive_after_validation):
            response = self.client.post(
                reverse("question_bank:bank_list"),
                self.bank_payload("不应创建的旧版课程题库"),
        )

        self.assertEqual(response.status_code, 403)
        self.assert_no_target_copy(before_question_count)

    def test_copy_service_rejects_a_stale_archived_target_course_without_creating_content(self):
        stale_course = Course.objects.get(pk=self.course.pk)
        Course.objects.filter(pk=self.course.pk).update(status=Course.Status.ARCHIVED)
        before_question_count = Question.objects.count()

        with self.assertRaises(PermissionDenied):
            copy_shared_bank(self.source, stale_course, self.teacher)

        self.assert_no_target_copy(before_question_count)

    def test_course_copy_redirects_with_an_error_when_its_loaded_source_is_archived(self):
        response = self.post_after_archiving_the_loaded_source(
            reverse("courses:copy_bank", args=[self.course.pk, self.source.pk]),
            "apps.question_bank.course_views.get_object_or_404",
        )

        self.assert_stale_source_copy_is_rejected(
            response,
            reverse("courses:banks", args=[self.course.pk]),
        )

    def test_legacy_copy_redirects_with_an_error_when_its_loaded_source_is_archived(self):
        response = self.post_after_archiving_the_loaded_source(
            reverse("question_bank:copy_bank", args=[self.source.pk]),
            "apps.question_bank.views.get_object_or_404",
        )

        self.assert_stale_source_copy_is_rejected(
            response,
            reverse("question_bank:bank_list"),
        )


class CourseQuestionBankScopeTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="teacher", role=User.Role.TEACHER)
        self.student = User.objects.create_user(
            username="S1",
            student_number="S1",
            role=User.Role.STUDENT,
        )
        self.course_a = Course.objects.create(
            name="课程 A", code="A", term="2026春", owner=self.teacher
        )
        self.course_b = Course.objects.create(
            name="课程 B", code="B", term="2026春", owner=self.teacher
        )
        self.client.force_login(self.teacher)

    def test_same_concept_name_is_isolated_by_course(self):
        first = Concept.objects.create(course=self.course_a, name="函数")
        second = Concept.objects.create(course=self.course_b, name="函数")

        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(first.normalized_name, second.normalized_name)

    def test_copy_shared_bank_clones_questions_options_and_course_concepts(self):
        source = QuestionBank.objects.create(
            course=self.course_a,
            created_by=self.teacher,
            name="共享题库",
            visibility=QuestionBank.Visibility.SHARED,
        )
        question = Question.objects.create(
            bank=source,
            sequence=1,
            question_type=Question.Type.SINGLE,
            text="1 + 1 = ?",
            correct_answer="B",
        )
        QuestionOption.objects.create(question=question, key="A", text="1")
        QuestionOption.objects.create(question=question, key="B", text="2")
        concept = Concept.objects.create(course=self.course_a, name="加法")
        QuestionConcept.objects.create(question=question, concept=concept, is_primary=True)

        copied = copy_shared_bank(source, self.course_b, self.teacher)

        self.assertEqual(copied.course, self.course_b)
        self.assertEqual(copied.created_by, self.teacher)
        copied_question = copied.questions.get()
        self.assertNotEqual(copied_question.pk, question.pk)
        self.assertEqual(copied_question.options.count(), 2)
        copied_concept = copied_question.primary_concept
        self.assertEqual(copied_concept.course, self.course_b)
        self.assertNotEqual(copied_concept.pk, concept.pk)

    def test_experiment_rejects_bank_or_student_from_another_course(self):
        bank_a = QuestionBank.objects.create(
            course=self.course_a,
            created_by=self.teacher,
            name="A 题库",
        )
        experiment = Experiment(
            course=self.course_b,
            created_by=self.teacher,
            question_bank=bank_a,
            name="错误实验",
        )
        with self.assertRaises(ValidationError):
            experiment.full_clean()

        class_a = ClassGroup.objects.create(course=self.course_a, name="一班")
        CourseEnrollment.objects.create(
            course=self.course_a,
            class_group=class_a,
            student=self.student,
        )
        valid_bank = QuestionBank.objects.create(
            course=self.course_b,
            created_by=self.teacher,
            name="B 题库",
        )
        valid_experiment = Experiment.objects.create(
            course=self.course_b,
            created_by=self.teacher,
            question_bank=valid_bank,
            name="B 实验",
        )
        link = ExperimentStudent(experiment=valid_experiment, student=self.student)
        with self.assertRaises(ValidationError):
            link.full_clean()

    def test_experiment_assignment_rejects_students_from_inactive_classes(self):
        class_group = ClassGroup.objects.create(course=self.course_a, name="停用班级", is_active=False)
        CourseEnrollment.objects.create(
            course=self.course_a,
            class_group=class_group,
            student=self.student,
        )
        bank = QuestionBank.objects.create(
            course=self.course_a,
            created_by=self.teacher,
            name="实验题库",
        )
        experiment = Experiment.objects.create(
            course=self.course_a,
            created_by=self.teacher,
            question_bank=bank,
            name="实验",
        )

        with self.assertRaisesMessage(ValidationError, "可用班级"):
            assign_students(experiment, [self.student])

    def test_course_bank_imports_questions_with_course_scoped_kc(self):
        from .test_unified_question_import import question_row, upload_rows
        bank = QuestionBank.objects.create(
            course=self.course_a,
            created_by=self.teacher,
            name="课程题库",
        )
        question_url = reverse("courses:question_import", args=[self.course_a.pk, bank.pk])
        Concept.objects.create(course=self.course_a, code="KC1", name="加法")
        Concept.objects.create(course=self.course_a, code="KC2", name="整数")
        question_file = upload_rows([question_row(题目='2+2=?', 题型='单选题', 选项A='3', 选项B='4', 答案='B', 三级知识点='')])

        preview = self.client.post(question_url, {"file": question_file})
        self.assertEqual(preview.status_code, 200)
        self.assertFalse(bank.questions.exists())
        preview_id = ImportPreviewBatch.objects.get().pk
        response = self.client.post(question_url, {"action": "confirm", "preview_id": preview_id})
        self.assertEqual(response.status_code, 200)
        question = bank.questions.get(sequence=1)
        self.assertEqual(question.primary_concept.course, self.course_a)
        self.assertEqual(
            set(question.concept_links.values_list("concept__name", flat=True)),
            {"加法", "整数"},
        )




    def test_course_kc_create_rename_and_safe_delete_are_audited(self):
        self.client.post(
            reverse("courses:concept_create", args=[self.course_a.pk]),
            {"sequence": "1", "name": "  基础运算  "},
        )
        concept = Concept.objects.get(course=self.course_a, normalized_name="基础运算")
        self.assertEqual(concept.code, "KC1")
        self.assertEqual(concept.name, "基础运算")
        self.assertTrue(AuditLog.objects.filter(action="concept.create", object_id=str(concept.pk)).exists())

        self.client.post(
            reverse("courses:concept_rename", args=[self.course_a.pk, concept.pk]),
            {"name": "算术基础"},
        )
        concept.refresh_from_db()
        self.assertEqual(concept.name, "算术基础")
        rename_audit = AuditLog.objects.get(action="concept.rename", object_id=str(concept.pk))
        self.assertEqual(rename_audit.details["old_name"], "基础运算")

        response = self.client.post(
            reverse("courses:concept_delete", args=[self.course_a.pk, concept.pk])
        )
        self.assertRedirects(response, reverse("courses:banks", args=[self.course_a.pk]))
        self.assertFalse(Concept.objects.filter(pk=concept.pk).exists())
        self.assertTrue(AuditLog.objects.filter(action="concept.delete", object_id=str(concept.pk)).exists())

    def test_course_kc_create_and_edit_keep_nodes_flat(self):
        root = Concept.objects.create(course=self.course_a, name="数学")
        self.client.post(
            reverse("courses:concept_create", args=[self.course_a.pk]),
            {
                "sequence": "2",
                "name": "代数",
                "description": "方程与结构",
                "parent": root.pk,
            },
        )
        branch = Concept.objects.get(course=self.course_a, name="代数")
        self.assertIsNone(branch.parent)
        self.assertEqual(branch.description, "方程与结构")

        self.client.post(
            reverse("courses:concept_rename", args=[self.course_a.pk, root.pk]),
            {"name": "数学基础", "description": "稳定节点"},
        )

        root.refresh_from_db()
        self.assertIsNone(root.parent)
        self.assertEqual(root.name, "数学基础")
        self.assertEqual(root.description, "稳定节点")

    def test_referenced_or_duplicate_course_kc_changes_are_rejected(self):
        bank = QuestionBank.objects.create(course=self.course_a, created_by=self.teacher, name="题库")
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="题目",
            correct_answer="答案",
        )
        referenced = Concept.objects.create(course=self.course_a, name="已引用")
        duplicate = Concept.objects.create(course=self.course_a, name="重复名称")
        QuestionConcept.objects.create(question=question, concept=referenced, is_primary=True)

        create_child = self.client.post(
            reverse("courses:concept_create", args=[self.course_a.pk]),
            {"sequence": "3", "name": "非法子节点", "parent": referenced.pk},
            follow=True,
        )
        self.assertTrue(
            Concept.objects.filter(course=self.course_a, name="非法子节点").exists()
        )

        movable = Concept.objects.create(course=self.course_a, name="待移动")
        move_under_referenced = self.client.post(
            reverse("courses:concept_rename", args=[self.course_a.pk, movable.pk]),
            {"name": movable.name, "parent": referenced.pk},
            follow=True,
        )
        movable.refresh_from_db()
        self.assertIsNone(movable.parent)

        delete = self.client.post(
            reverse("courses:concept_delete", args=[self.course_a.pk, referenced.pk])
        )
        self.assertRedirects(delete, reverse("courses:banks", args=[self.course_a.pk]))
        referenced.refresh_from_db()
        self.assertEqual(referenced.status, Concept.Status.ARCHIVED)

        rename = self.client.post(
            reverse("courses:concept_rename", args=[self.course_a.pk, referenced.pk]),
            {"name": duplicate.name},
        )
        self.assertRedirects(rename, reverse("courses:banks", args=[self.course_a.pk]))
        referenced.refresh_from_db()
        self.assertEqual(referenced.name, "已引用")

    def test_course_kc_create_and_rename_handle_database_duplicate_races(self):
        concept = Concept.objects.create(course=self.course_a, name="原名称")

        with patch(
            "apps.question_bank.course_views.CourseConceptForm.save",
            side_effect=IntegrityError("unique_course_concept"),
        ):
            create_response = self.client.post(
                reverse("courses:concept_create", args=[self.course_a.pk]),
                {"sequence": "2", "name": "并发名称"},
                follow=True,
            )
            rename_response = self.client.post(
                reverse("courses:concept_rename", args=[self.course_a.pk, concept.pk]),
                {"name": "并发改名"},
                follow=True,
            )

        self.assertEqual(create_response.status_code, 200)
        self.assertEqual(rename_response.status_code, 200)
        concept.refresh_from_db()
        self.assertEqual(concept.name, "原名称")

    def test_course_kc_search_is_normalized_limited_and_course_scoped(self):
        for index in range(14):
            Concept.objects.create(course=self.course_a, name=f"基础知识 {index:02d}")
        Concept.objects.create(course=self.course_a, name="无关概念")
        Concept.objects.create(course=self.course_b, name="基础知识 外部")

        response = self.client.get(
            reverse("courses:concept_search", args=[self.course_a.pk]),
            {"q": "  基础知识  "},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["results"]), 12)
        self.assertTrue(all(set(item) == {"id", "name"} for item in payload["results"]))
        self.assertTrue(all("基础知识" in item["name"] for item in payload["results"]))
        self.assertNotIn("基础知识 外部", [item["name"] for item in payload["results"]])

    def test_question_edit_saves_one_primary_and_multiple_secondary_course_kcs(self):
        bank = QuestionBank.objects.create(course=self.course_a, created_by=self.teacher, name="章节题库")
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="原题目",
            correct_answer="原答案",
        )
        primary = Concept.objects.create(course=self.course_a, name="基础运算")
        secondary_a = Concept.objects.create(course=self.course_a, name="整数")
        secondary_b = Concept.objects.create(course=self.course_a, name="近似")
        url = reverse("courses:question_edit", args=[self.course_a.pk, bank.pk, question.pk])

        response = self.client.post(
            url,
            {
                "text": "更新题目",
                "question_type": Question.Type.SHORT,
                "status": Question.Status.ACTIVE,
                "correct_answer_text": "更新答案",
                "accepted_answers_text": "",
                "primary_concept": primary.pk,
                "secondary_concepts": [secondary_a.pk, secondary_b.pk],
            },
        )

        self.assertRedirects(response, reverse("courses:bank_detail", args=[self.course_a.pk, bank.pk]))
        links = question.concept_links.select_related("concept")
        self.assertEqual(links.get(is_primary=True).concept, primary)
        self.assertSetEqual(
            set(links.filter(is_primary=False).values_list("concept__name", flat=True)),
            {"整数", "近似"},
        )

    def test_question_edit_rejects_cross_course_or_duplicate_kc_values_atomically(self):
        bank = QuestionBank.objects.create(course=self.course_a, created_by=self.teacher, name="章节题库")
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="保持原题目",
            correct_answer="答案",
        )
        existing = Concept.objects.create(course=self.course_a, name="原 KC")
        local = Concept.objects.create(course=self.course_a, name="本课程 KC")
        foreign = Concept.objects.create(course=self.course_b, name="外部 KC")
        QuestionConcept.objects.create(question=question, concept=existing, is_primary=True)
        url = reverse("courses:question_edit", args=[self.course_a.pk, bank.pk, question.pk])
        base_payload = {
            "text": "不应保存",
            "question_type": Question.Type.SHORT,
            "status": Question.Status.ACTIVE,
            "correct_answer_text": "新答案",
            "accepted_answers_text": "",
        }

        cross_course = self.client.post(
            url,
            {**base_payload, "primary_concept": foreign.pk},
        )
        self.assertEqual(cross_course.status_code, 200)
        question.refresh_from_db()
        self.assertEqual(question.text, "保持原题目")
        self.assertEqual(question.primary_concept, existing)

        duplicate_primary = self.client.post(
            url,
            {
                **base_payload,
                "primary_concept": local.pk,
                "secondary_concepts": [local.pk],
            },
        )
        self.assertEqual(duplicate_primary.status_code, 200)
        question.refresh_from_db()
        self.assertEqual(question.text, "保持原题目")
        self.assertEqual(question.primary_concept, existing)

        duplicate_secondary = self.client.post(
            url,
            {
                **base_payload,
                "primary_concept": local.pk,
                "secondary_concepts": [existing.pk, existing.pk],
            },
        )
        self.assertEqual(duplicate_secondary.status_code, 200)
        question.refresh_from_db()
        self.assertEqual(question.text, "保持原题目")

    def test_question_edit_rejects_orphan_secondary_kcs_without_losing_mapping(self):
        bank = QuestionBank.objects.create(course=self.course_a, created_by=self.teacher, name="章节题库")
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="保持原题目",
            correct_answer="原答案",
        )
        existing = Concept.objects.create(course=self.course_a, name="原主 KC")
        secondary = Concept.objects.create(course=self.course_a, name="候选次 KC")
        QuestionConcept.objects.create(question=question, concept=existing, is_primary=True)
        url = reverse("courses:question_edit", args=[self.course_a.pk, bank.pk, question.pk])

        response = self.client.post(
            url,
            {
                "text": "不应保存",
                "question_type": Question.Type.SHORT,
                "status": Question.Status.ACTIVE,
                "correct_answer_text": "新答案",
                "accepted_answers_text": "",
                "primary_concept": "",
                "secondary_concepts": [secondary.pk],
            },
        )

        self.assertEqual(response.status_code, 200)
        question.refresh_from_db()
        self.assertEqual(question.text, "保持原题目")
        self.assertEqual(question.primary_concept, existing)
        self.assertFalse(question.concept_links.filter(concept=secondary).exists())

    def test_class_and_kc_renames_do_not_rewrite_published_snapshots(self):
        class_group = ClassGroup.objects.create(
            course=self.course_a,
            name="发布时班级",
            academic_year="2026",
        )
        CourseEnrollment.objects.create(
            course=self.course_a,
            class_group=class_group,
            student=self.student,
        )
        bank = QuestionBank.objects.create(course=self.course_a, created_by=self.teacher, name="发布题库")
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="发布题目",
            correct_answer="答案",
        )
        concept = Concept.objects.create(course=self.course_a, name="发布时 KC")
        QuestionConcept.objects.create(question=question, concept=concept, is_primary=True)
        experiment = Experiment.objects.create(
            course=self.course_a,
            created_by=self.teacher,
            question_bank=bank,
            name="快照实验",
            sampling_mode=Experiment.SamplingMode.TOTAL_COUNT,
            sampling_total_count=1,
        )
        ExperimentSamplingKC.objects.create(experiment=experiment, concept=concept)
        sync_experiment_questions(experiment)
        plan = get_or_create_publish_plan(experiment, self.teacher)
        plan = save_plan_selection(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
            class_ids=[class_group.pk],
            exclusions_by_class={},
        )
        generate_preview(
            plan, self.teacher, expected_revision=plan.plan_revision
        )
        plan.refresh_from_db()
        commit_publish_plan(
            plan, self.teacher, expected_revision=plan.plan_revision
        )
        experiment.refresh_from_db()
        version = experiment.frozen_release_snapshot

        self.client.post(
            reverse("courses:concept_rename", args=[self.course_a.pk, concept.pk]),
            {"name": "改名后 KC"},
        )
        class_form = ClassGroupUpdateForm(instance=class_group)
        self.client.post(
            reverse("courses:class_update", args=[self.course_a.pk, class_group.pk]),
            {
                "name": "改名后班级",
                "academic_year": class_group.academic_year,
                "is_active": "on",
                "state_token": class_form.initial["state_token"],
            },
        )

        snapshot = version.question_snapshots.get()
        assignment = experiment.student_links.get(student=self.student)
        class_group.refresh_from_db()
        self.assertEqual(class_group.name, "改名后班级")
        self.assertEqual(snapshot.primary_kc, "发布时 KC")
        self.assertEqual(snapshot.all_kcs, ["发布时 KC"])
        self.assertEqual(assignment.class_snapshot_name, "2026 发布时班级")

    def test_course_kc_writes_are_post_only_csrf_protected_and_archived_read_only(self):
        concept = Concept.objects.create(course=self.course_a, name="安全 KC")
        self.assertEqual(
            self.client.get(reverse("courses:concept_delete", args=[self.course_a.pk, concept.pk])).status_code,
            405,
        )

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.teacher)
        self.assertEqual(
            csrf_client.post(
                reverse("courses:concept_rename", args=[self.course_a.pk, concept.pk]),
                {"name": "无令牌改名"},
            ).status_code,
            403,
        )

        self.course_a.status = Course.Status.ARCHIVED
        self.course_a.save(update_fields=["status"])
        self.assertEqual(
            self.client.post(
                reverse("courses:concept_rename", args=[self.course_a.pk, concept.pk]),
                {"name": "归档改名"},
            ).status_code,
            403,
        )
        page = self.client.get(reverse("courses:banks", args=[self.course_a.pk]))
        self.assertEqual(page.status_code, 200)
