from apps.api.test_support import domain_payload
import json
import os
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import ClassGroup, Course, User
from apps.experiments.models import Experiment, ExperimentQuestion
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept


class StandaloneExperimentApiTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="standalone-experiment-teacher",
            role=User.Role.TEACHER,
        )
        self.other_teacher = User.objects.create_user(
            username="standalone-experiment-other-teacher",
            role=User.Role.TEACHER,
        )
        self.student = User.objects.create_user(
            username="standalone-experiment-student",
            student_number="STANDALONE-01",
            display_name="独立学生",
            role=User.Role.STUDENT,
        )
        self.class_group = ClassGroup.objects.create(
            owner=self.teacher,
            name="独立实验班",
            academic_year="2026",
        )
        self.student.class_group = self.class_group
        self.student.save(update_fields=["class_group"])
        self.bank = QuestionBank.objects.create(
            owner=self.teacher,
            name="独立实验题库",
        )
        self.question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="独立实验题目",
            correct_answer="答案",
        )
        concept = Concept.objects.create(
            owner=self.teacher,
            scope=Concept.Scope.PRIVATE,
            name="独立 KC",
            normalized_name="独立 kc",
        )
        QuestionConcept.objects.create(
            question=self.question,
            concept=concept,
            is_primary=True,
        )
        self.experiment = Experiment.objects.create(
            owner=self.teacher,
            question_bank=self.bank,
            name="独立实验",
            description="保留旧版实验流程",
            feedback_mode=Experiment.Feedback.CORRECTNESS,
        )
        ExperimentQuestion.objects.create(
            experiment=self.experiment,
            question=self.question,
            display_order=1,
        )

    @property
    def api_root(self):
        return "/api/v1/teacher/standalone-experiments/"

    def test_collection_and_detail_are_explicit_standalone_projections(self):
        self.client.force_login(self.teacher)

        collection = self.client.get(self.api_root)
        detail = self.client.get(f"{self.api_root}{self.experiment.pk}/")

        self.assertEqual(collection.status_code, 200, collection.content)
        self.assertEqual(domain_payload(collection)["kind"], "standalone")
        self.assertEqual(domain_payload(collection)["items"][0]["id"], self.experiment.pk)
        self.assertEqual(
            domain_payload(collection)["question_banks"],
            [{"id": self.bank.pk, "name": self.bank.name}],
        )
        self.assertEqual(detail.status_code, 200, detail.content)
        payload = domain_payload(detail)
        self.assertEqual(payload["kind"], "standalone")
        self.assertEqual(payload["experiment"]["question_count"], 1)
        self.assertEqual(payload["experiment"]["feedback_mode_label"], "显示正误")
        self.assertEqual(payload["selection"]["current_candidate_count"], 1)
        self.assertEqual(
            payload["question_banks"],
            [{"id": self.bank.pk, "name": self.bank.name}],
        )
        self.assertEqual(payload["available_students"][0]["id"], self.student.pk)
        self.assertIn("publish_url", payload["actions"])
        self.assertNotIn("correct_answer", payload["experiment"])

    def test_create_assign_publish_and_results_reuse_legacy_services(self):
        self.client.force_login(self.teacher)
        create = self.client.post(
            self.api_root,
            data=json.dumps(
                {
                    "name": "API 新建独立实验",
                    "description": "通过独立兼容接口创建",
                    "question_bank_id": self.bank.pk,
                    "selection_mode": Experiment.SelectionMode.ALL,
                    "ordering_mode": Experiment.Ordering.RANDOM,
                    "feedback_mode": Experiment.Feedback.FULL,
                    "feedback_release_policy": Experiment.FeedbackReleasePolicy.AFTER_PERSONAL_SUBMISSION,
                    "time_mode": Experiment.TimeMode.TOTAL,
                    "total_time_limit_minutes": 30,
                    "per_question_time_limit_seconds": None,
                    "resume_mode": Experiment.ResumeMode.RESUMABLE,
                    "confidence_mode": Experiment.ConfidenceMode.OFF,
                    "confidence_sampling_unit": Experiment.ConfidenceSamplingUnit.ATTEMPT,
                    "heartbeat_interval_seconds": 30,
                    "question_quotas": [],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(create.status_code, 201, create.content)
        created_id = domain_payload(create)["experiment"]["id"]
        created = Experiment.objects.get(pk=created_id)
        self.assertIsNone(created.course_id)
        self.assertEqual(created.question_links.count(), 1)

        assignment = self.client.post(
            f"{self.api_root}{created_id}/students/",
            data=json.dumps({"student_ids": [self.student.pk]}),
            content_type="application/json",
        )
        self.assertEqual(assignment.status_code, 200, assignment.content)
        self.assertEqual(created.student_links.count(), 1)

        publish = self.client.post(f"{self.api_root}{created_id}/publish/")
        self.assertEqual(publish.status_code, 200, publish.content)
        created.refresh_from_db()
        self.assertEqual(created.status, Experiment.Status.PUBLISHED)

        results = self.client.get(f"{self.api_root}{created_id}/results/?status=completed")
        self.assertEqual(results.status_code, 200, results.content)
        result_payload = domain_payload(results)
        self.assertEqual(result_payload["kind"], "standalone")
        self.assertEqual(result_payload["selected_status"], "completed")
        self.assertEqual(
            result_payload["actions"]["identified_export_url"],
            reverse("teacher-experiment-identified-export", args=[created_id]),
        )

    def test_course_backed_global_urls_resolve_to_the_course_canonical_destination(self):
        course = Course.objects.create(
            owner=self.teacher,
            name="全局入口课程",
            code="GLOBAL-EXP",
            term="2026 秋",
        )
        course_bank = QuestionBank.objects.create(
            owner=self.teacher,
            course=course,
            name="课程实验题库",
        )
        course_experiment = Experiment.objects.create(
            owner=self.teacher,
            course=course,
            question_bank=course_bank,
            name="课程实验",
        )
        self.client.force_login(self.teacher)

        collection = self.client.get(self.api_root)
        self.assertEqual(collection.status_code, 200, collection.content)
        self.assertEqual(
            domain_payload(collection),
            {"kind": "canonical", "canonical_url": reverse("courses:list")},
        )

        for destination, expected in (
            ("detail", reverse("courses:experiment_detail", args=[course.pk, course_experiment.pk])),
            ("edit", reverse("courses:experiment_edit", args=[course.pk, course_experiment.pk])),
        ):
            with self.subTest(destination=destination):
                response = self.client.get(
                    f"{self.api_root}{course_experiment.pk}/?destination={destination}"
                )
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(domain_payload(response), {"kind": "canonical", "canonical_url": expected})

        results = self.client.get(f"{self.api_root}{course_experiment.pk}/results/")
        self.assertEqual(results.status_code, 200, results.content)
        self.assertEqual(
            domain_payload(results),
            {
                "kind": "canonical",
                "canonical_url": reverse(
                    "courses:experiment_results", args=[course.pk, course_experiment.pk]
                ),
            },
        )


    def test_other_teacher_cannot_discover_or_mutate_a_standalone_experiment(self):
        self.client.force_login(self.other_teacher)

        detail = self.client.get(f"{self.api_root}{self.experiment.pk}/")
        update = self.client.patch(
            f"{self.api_root}{self.experiment.pk}/",
            data=json.dumps({"name": "不应更新"}),
            content_type="application/json",
        )

        self.assertEqual(detail.status_code, 404)
        self.assertEqual(update.status_code, 404)

    def test_student_role_and_missing_csrf_are_rejected_before_writes(self):
        self.client.force_login(self.student)
        forbidden = self.client.post(
            self.api_root,
            data=json.dumps({"name": "学生不能创建"}),
            content_type="application/json",
        )
        self.assertEqual(forbidden.status_code, 403)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.teacher)
        rejected = csrf_client.post(
            self.api_root,
            data=json.dumps({"name": "缺少 CSRF"}),
            content_type="application/json",
        )
        self.assertEqual(rejected.status_code, 403)

    def test_archived_bank_is_rejected_without_replacing_question_links(self):
        self.client.force_login(self.teacher)
        before_links = list(
            self.experiment.question_links.values_list("question_id", "display_order")
        )
        QuestionBank.objects.filter(pk=self.bank.pk).update(
            visibility=QuestionBank.Visibility.ARCHIVED
        )

        response = self.client.post(
            f"{self.api_root}{self.experiment.pk}/sync-questions/",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(
            list(
                self.experiment.question_links.values_list("question_id", "display_order")
            ),
            before_links,
        )
