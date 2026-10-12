from apps.api.test_support import domain_payload
import json

from django.test import TestCase
from django.core.exceptions import ValidationError

from apps.accounts.models import Course, User
from .forms import QuestionEditForm
from .models import Concept, Question, QuestionBank, QuestionConcept, QuestionOption


class QuestionReadinessTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="ready-teacher", role=User.Role.TEACHER)
        self.course = Course.objects.create(owner=self.teacher, name="状态课程", code="READY", term="2026")
        self.bank = QuestionBank.objects.create(course=self.course, owner=self.teacher, created_by=self.teacher, name="状态题库")
        self.kc = Concept.objects.create(course=self.course, code="KC1", name="基本知识")
        self.client.force_login(self.teacher)

    def question(self, *, answer="答案", mapped=True, status=Question.Status.DRAFT):
        question = Question.objects.create(bank=self.bank, sequence=self.bank.questions.count() + 1, text="题干", question_type=Question.Type.SHORT, correct_answer=answer, status=Question.Status.DRAFT)
        if mapped:
            QuestionConcept.objects.create(question=question, concept=self.kc, is_primary=True)
        if status == Question.Status.ARCHIVED:
            question.status = status
            question.save(update_fields=["status"])
        return question

    def form(self, question, **changes):
        data = {
            "text": question.text, "question_type": question.question_type, "status": "active",
            "correct_answer_text": question.correct_answer,
            "kc_mapping_json": json.dumps({"root_kc_code": "KC1", "nodes": [{"kc_code": "KC1", "parent_kc_code": None, "order": 1}]}),
        }
        return QuestionEditForm({**data, **changes}, instance=question, course=self.course)

    def test_complete_draft_becomes_active_on_save_without_optional_fields(self):
        question = self.question()
        form = self.form(question, status="draft")
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        question.refresh_from_db()
        self.assertEqual(question.status, Question.Status.ACTIVE)
        self.assertIsNone(question.teacher_difficulty)
        self.assertEqual(question.explanation, "")

    def test_missing_answer_and_mapping_are_saved_as_explained_draft(self):
        question = self.question(mapped=False)
        form = self.form(question, correct_answer_text="", kc_mapping_json="{}")
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        response = self.client.get(f"/api/v1/teacher/courses/{self.course.pk}/banks/{self.bank.pk}/questions/")
        row = domain_payload(response)["items"][0]
        self.assertEqual(row["status"], "draft")
        self.assertTrue(any("答案" in message for message in row["draft_reasons"]))
        self.assertTrue(any("KC" in message for message in row["draft_reasons"]))

    def test_invalid_choice_reference_can_be_saved_then_corrected(self):
        question = self.question()
        form = self.form(question, question_type="single_choice", option_a="甲", option_b="乙", correct_answer_text="C")
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        question.refresh_from_db()
        self.assertEqual(question.status, "draft")
        self.assertEqual(question.correct_answer, "C")
        form = self.form(question, option_a="甲", option_b="乙", correct_answer_text="A")
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        question.refresh_from_db()
        self.assertEqual(question.status, "active")

    def test_recheck_promotes_complete_drafts_and_preserves_archived_questions(self):
        complete = self.question()
        incomplete = self.question(answer="")
        archived = self.question(status=Question.Status.ARCHIVED)
        response = self.client.post(f"/api/v1/teacher/courses/{self.course.pk}/banks/{self.bank.pk}/questions/recheck/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual({key: domain_payload(response)[key] for key in ("checked", "activated", "draft")}, {"checked": 2, "activated": 1, "draft": 1})
        for question, expected in ((complete, "active"), (incomplete, "draft"), (archived, "archived")):
            question.refresh_from_db()
            self.assertEqual(question.status, expected)
        repeated = self.client.post(f"/api/v1/teacher/courses/{self.course.pk}/banks/{self.bank.pk}/questions/recheck/")
        self.assertEqual(domain_payload(repeated)["activated"], 0)

    def test_mapping_completion_automatically_rechecks_existing_draft(self):
        from .services.question_kc_mapping import replace_question_kc_mapping
        question = self.question(mapped=False)
        replace_question_kc_mapping(question, root_kc_code="KC1", nodes=[{"kc_code": "KC1", "parent_kc_code": None, "order": 1}])
        question.refresh_from_db()
        self.assertEqual(question.status, "active")

    def test_false_and_zero_are_valid_answers(self):
        for question_type, answer in (("numeric", "0"), ("true_false", "错误")):
            question = self.question()
            form = self.form(question, question_type=question_type, correct_answer_text=answer)
            self.assertTrue(form.is_valid(), form.errors)
            form.save()
            question.refresh_from_db()
            self.assertEqual(question.status, "active")

    def test_empty_true_false_answer_is_not_displayed_as_false(self):
        question = self.question(answer="")
        question.question_type = Question.Type.TRUE_FALSE
        question.save(update_fields=["question_type"])
        form = QuestionEditForm(instance=question, course=self.course)
        self.assertEqual(form.fields["correct_answer_text"].initial, "")

    def test_empty_json_answer_cannot_be_activated(self):
        from .services.question_readiness import refresh_question_readiness
        question = self.question(answer=[])
        issues = refresh_question_readiness(question)
        self.assertEqual(question.status, Question.Status.DRAFT)
        self.assertTrue(any("答案不能为空" in issue for issue in issues))

    def test_recheck_is_authorized_and_archived_banks_are_read_only(self):
        question = self.question()
        url = f"/api/v1/teacher/courses/{self.course.pk}/banks/{self.bank.pk}/questions/recheck/"
        outsider = User.objects.create_user(username="readiness-outsider", role=User.Role.TEACHER)
        self.client.force_login(outsider)
        self.assertEqual(self.client.post(url).status_code, 404)
        self.client.force_login(self.teacher)
        self.bank.visibility = QuestionBank.Visibility.ARCHIVED
        self.bank.save(update_fields=["visibility"])
        self.assertEqual(self.client.post(url).status_code, 403)
        question.refresh_from_db()
        self.assertEqual(question.status, Question.Status.DRAFT)

    def test_publish_reuses_content_readiness_rules(self):
        from apps.experiments.models import Experiment
        from apps.experiments.services import validate_for_publish
        from .services.question_readiness import readiness_issues
        question = self.question(answer="")
        Question.objects.filter(pk=question.pk).update(status=Question.Status.ACTIVE)
        experiment = Experiment.objects.create(course=self.course, question_bank=self.bank, owner=self.teacher, name="一致校验")
        with self.assertRaises(ValidationError) as failed:
            validate_for_publish(experiment, participant_count=1)
        self.assertIn(readiness_issues(question)[0], failed.exception.messages)

    def test_recheck_keeps_published_snapshots_unchanged(self):
        from apps.experiments.models import Experiment, ExperimentVersion, QuestionSnapshot
        question = self.question()
        experiment = Experiment.objects.create(course=self.course, question_bank=self.bank, owner=self.teacher, name="历史实验", status=Experiment.Status.PUBLISHED)
        version = ExperimentVersion.objects.create(experiment=experiment, version_number=1, published_by=self.teacher, configuration={})
        snapshot = QuestionSnapshot.objects.create(version=version, source_question=question, source_sequence=question.sequence, code=question.code, text="发布时题干", question_type=question.question_type, correct_answer="发布时答案", primary_kc="发布时 KC")
        identity = question.course_identity.pk
        response = self.client.post(f"/api/v1/teacher/courses/{self.course.pk}/banks/{self.bank.pk}/questions/recheck/")
        self.assertEqual(response.status_code, 200)
        snapshot.refresh_from_db()
        question.refresh_from_db()
        self.assertEqual(question.course_identity.pk, identity)
        self.assertEqual((snapshot.text, snapshot.correct_answer, snapshot.primary_kc), ("发布时题干", "发布时答案", "发布时 KC"))
