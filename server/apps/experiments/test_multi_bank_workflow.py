from apps.api.test_support import domain_payload
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept
from apps.question_bank.services.lifecycle import delete_question_bank
from .models import Experiment, ExperimentQuestionBank, ExperimentSamplingKC, PublishPreviewParticipant
from .publication_services import (
    PublicationConflict, commit_publish_plan, generate_preview,
    get_or_create_publish_plan, save_plan_selection,
)
from .sampling import SamplingService


class MultiBankWorkflowTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="multi-bank", role="teacher")
        self.course = Course.objects.create(owner=self.teacher, name="课程", code="MB", term="2026")
        self.banks = [QuestionBank.objects.create(owner=self.teacher, course=self.course, name=name)
                      for name in ["第一章", "第二章", "第三章"]]
        self.concept = Concept.objects.create(course=self.course, code="KC1", name="概念")
        for bank in self.banks[:2]:
            for sequence in range(1, 5):
                question = Question.objects.create(bank=bank, sequence=sequence,
                    text=f"{bank.name} {sequence}", question_type=Question.Type.SHORT, correct_answer="答案")
                QuestionConcept.objects.create(question=question, concept=self.concept, is_primary=True)
        self.experiment = Experiment.objects.create(owner=self.teacher, course=self.course,
            question_bank=self.banks[0], name="多题库实验", sampling_mode="total_count", sampling_total_count=3)
        self.url = f"/api/v1/teacher/courses/{self.course.pk}/experiments/"
        self.client.force_login(self.teacher)

    def select_banks(self, ids=None):
        response = self.client.patch(f"{self.url}{self.experiment.pk}/",
            {"question_bank_ids": ids if ids is not None else [bank.pk for bank in self.banks[:2]]},
            content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.experiment.refresh_from_db()
        return domain_payload(response)

    def preview(self):
        group = ClassGroup.objects.create(course=self.course, name="一班", academic_year="2026")
        for index in range(4):
            student = User.objects.create_user(username=f"mb-{index}", student_number=f"mb-{index}", role="student")
            CourseEnrollment.objects.create(course=self.course, class_group=group, student=student)
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        save_plan_selection(plan, self.teacher, expected_revision=plan.plan_revision,
            class_ids=[group.pk], exclusions_by_class={})
        plan.refresh_from_db()
        run = generate_preview(plan, self.teacher, expected_revision=plan.plan_revision)
        plan.refresh_from_db()
        return plan, run

    def test_create_edit_roundtrip_and_legacy_single_input(self):
        data = self.select_banks()
        self.assertEqual(data["question_bank_ids"], [bank.pk for bank in self.banks[:2]])
        self.assertEqual([bank["name"] for bank in data["question_banks"]], ["第一章", "第二章"])
        response = self.client.post(self.url, {"name": "新增", "question_bank_ids": [bank.pk for bank in self.banks],
            "sampling": {"mode": "total_count", "total_count": 2, "match_scope": "any_level", "nodes": []}},
            content_type="application/json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(len(domain_payload(response)["question_banks"]), 3)
        response = self.client.patch(f"{self.url}{self.experiment.pk}/", {"question_bank_id": self.banks[1].pk}, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(domain_payload(response)["question_bank_ids"], [self.banks[1].pk])

    def test_missing_bank_is_field_error(self):
        response = self.client.post(self.url, {"name": "无题库", "sampling": {"mode": "total_count", "total_count": 1, "nodes": []}}, content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_invalid_sets(self):
        other = Course.objects.create(owner=self.teacher, name="外部", code="OTHER", term="2026")
        foreign = QuestionBank.objects.create(course=other, name="外部")
        archived = QuestionBank.objects.create(course=self.course, name="归档", visibility="archived")
        for ids in [[], [self.banks[0].pk] * 2, [foreign.pk], [archived.pk], [999999]]:
            with self.subTest(ids=ids):
                response = self.client.patch(f"{self.url}{self.experiment.pk}/", {"question_bank_ids": ids}, content_type="application/json")
                self.assertEqual(response.status_code, 400, response.content)

    def test_inventory_and_canonical_candidate_order(self):
        self.select_banks()
        response = self.client.post(f"{self.url}sampling-inventory/", {
            "question_bank_ids": [bank.pk for bank in self.banks], "kc_codes": ["KC1"], "match_scope": "any_level"}, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(domain_payload(response)["candidate_count"], 8)
        self.assertEqual([row["candidate_count"] for row in domain_payload(response)["bank_counts"]], [4, 4, 0])
        service = SamplingService(self.experiment)
        candidates = service.resolve().candidates
        self.assertEqual(len(candidates), 8)
        self.assertEqual([(q.bank_id, q.sequence) for q in candidates], sorted((q.bank_id, q.sequence) for q in candidates))
        self.assertEqual(service.select_with_attribution("1" * 64), service.select_with_attribution("1" * 64))
        selections = {
            tuple(question.pk for question in service.select(f"{seed:016x}" + "0" * 48))
            for seed in range(16)
        }
        self.assertGreater(len(selections), 1)

    def test_empty_bank_change_invalidates_preview_and_commit_is_atomic(self):
        self.select_banks()
        plan, _ = self.preview()
        self.select_banks([bank.pk for bank in self.banks])
        with self.assertRaises(PublicationConflict):
            commit_publish_plan(plan, self.teacher, expected_revision=plan.plan_revision)
        self.assertFalse(self.experiment.class_tasks.exists())
        self.assertFalse(self.experiment.versions.exists())

    def test_publish_freezes_sources_and_guards_relation_writes(self):
        self.select_banks()
        plan, run = self.preview()
        participants = list(PublishPreviewParticipant.objects.filter(class_result__preview_run=run))
        assignments = {p.student_id: list(p.items.values_list("source_question_id", flat=True)) for p in participants}
        self.assertEqual(len({p.sampling_seed for p in participants}), 4)
        commit_publish_plan(plan, self.teacher, expected_revision=plan.plan_revision)
        self.experiment.refresh_from_db()
        release = self.experiment.frozen_release_snapshot
        for participant in release.participant_snapshots.all():
            self.assertEqual([row["source_question_id"] for row in participant.assignment_snapshot], assignments[participant.student_id])
            self.assertTrue(all(row["sampling_source"]["source_bank_id"] in {bank.pk for bank in self.banks[:2]} for row in participant.assignment_snapshot))
        self.assertEqual(release.configuration["question_bank_ids"], [bank.pk for bank in self.banks[:2]])
        self.assertEqual({q.source_bank_id for q in release.question_snapshots.all()}, {bank.pk for bank in self.banks[:2]})
        self.banks[0].name = "后来改名"
        self.banks[0].save()
        data = domain_payload(self.client.get(f"{self.url}{self.experiment.pk}/"))
        self.assertEqual(data["question_banks"][0]["name"], "第一章")
        self.banks[0].visibility = "archived"
        self.banks[0].save()
        readiness = domain_payload(self.client.get(f"{self.url}{self.experiment.pk}/readiness/"))
        self.assertTrue(readiness["ready"], readiness)
        Question.objects.filter(bank__in=self.banks[:2]).update(status="retired")
        readiness = domain_payload(self.client.get(f"{self.url}{self.experiment.pk}/readiness/"))
        self.assertTrue(readiness["ready"], readiness)
        self.assertEqual(readiness["candidate_count"], 8)
        with self.assertRaises(ValidationError):
            self.experiment.question_bank_links.all().delete()
        with self.assertRaises(ValidationError):
            self.experiment.question_bank_links.update(selection_order=10)
        with self.assertRaises(ValidationError):
            ExperimentQuestionBank.objects.bulk_create([ExperimentQuestionBank(experiment=self.experiment, question_bank=self.banks[2], selection_order=2)])

    def test_deleting_draft_bank_keeps_remaining_sources(self):
        self.select_banks()
        delete_question_bank(self.banks[1])
        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.selected_question_bank_ids, [self.banks[0].pk])

    def test_kc_and_type_quotas_and_history_filter_use_the_union(self):
        self.select_banks()
        self.experiment.sampling_total_count = 6
        self.experiment.save()
        self.experiment.question_quotas.filter(question_type=Question.Type.SHORT).update(amount=6)
        service = SamplingService(self.experiment)
        self.assertEqual(len(service.select_with_attribution("2" * 64)), 6)
        self.experiment.question_quotas.update(amount=0)
        self.experiment.sampling_mode = "kc_quota"
        self.experiment.sampling_total_count = None
        self.experiment.save()
        ExperimentSamplingKC.objects.create(experiment=self.experiment, concept=self.concept, quota=6)
        self.assertEqual(len(SamplingService(self.experiment).select_with_attribution("3" * 64)), 6)
        self.experiment.sampling_kcs.all().delete()
        self.experiment.sampling_mode = "total_count"
        self.experiment.sampling_total_count = 3
        self.experiment.cross_experiment_item_policy = "exclude_previous"
        self.experiment.save()
        history = set(self.banks[0].questions.values_list("pk", flat=True))
        items = SamplingService(self.experiment, assigned_question_ids=history).select_with_attribution("4" * 64)
        self.assertEqual({item["question"].bank_id for item in items}, {self.banks[1].pk})
        self.experiment.cross_experiment_item_policy = "mixed_review"
        self.experiment.review_count = 1
        self.experiment.save()
        items = SamplingService(self.experiment, completed_question_ids=history).select_with_attribution("5" * 64)
        self.assertEqual(sum(item["question"].pk in history for item in items), 1)

    def test_unauthorized_teacher_cannot_read_or_change_selection(self):
        other = User.objects.create_user(username="unauthorized", role="teacher")
        self.client.force_login(other)
        response = self.client.post(f"{self.url}sampling-inventory/", {"question_bank_ids": [self.banks[0].pk], "kc_codes": [], "match_scope": "primary"}, content_type="application/json")
        self.assertEqual(response.status_code, 404)

    def test_repeated_preview_and_reordered_bank_input_preserve_assignments(self):
        self.select_banks()
        plan, run = self.preview()
        def assignments(preview):
            return {p.student_id: list(p.items.values_list("source_question_id", flat=True))
                    for p in PublishPreviewParticipant.objects.filter(class_result__preview_run=preview)}
        before = assignments(run)
        self.select_banks([bank.pk for bank in reversed(self.banks[:2])])
        next_run = generate_preview(plan, self.teacher, expected_revision=plan.plan_revision)
        self.assertEqual(assignments(next_run), before)
