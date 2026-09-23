import json
import uuid
from io import StringIO
from collections import Counter
from datetime import timedelta

from django.db import connection
from django.core.management import call_command
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.question_bank.models import (
    Concept,
    Question,
    QuestionBank,
    QuestionConcept,
)
from apps.assessment.models import AssessmentEvent, Attempt, ExperimentSession
from apps.assessment.deadline_services import DeadlineDecisionService
from apps.assessment.services import get_or_create_session, record_visit, resume_task_session, save_draft, submit_attempt, _terminate_for_deadline
from apps.assessment.test_support import accept_research_consent
from apps.analytics.recording_coverage import generate_session_coverage
from .publication_services import append_schedule_revision, manual_close_task

from .models import (
    ClassExperimentTask,
    Experiment,
    ExperimentSamplingKC,
    PublishPlan,
    PublishOperationJob,
)
from .publication_jobs import run_publish_operation


class TeacherExperimentApiTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="experiment-api-teacher",
            password="test-password",
            role=User.Role.TEACHER,
        )
        self.other_teacher = User.objects.create_user(
            username="experiment-api-other",
            password="test-password",
            role=User.Role.TEACHER,
        )
        self.student = User.objects.create_user(
            username="experiment-api-student",
            password="test-password",
            role=User.Role.STUDENT,
        )
        self.course = Course.objects.create(
            name="实验 API 课程",
            code="EXP-API",
            term="2026-Autumn",
            owner=self.teacher,
        )
        self.bank = QuestionBank.objects.create(
            name="实验 API 题库",
            course=self.course,
            owner=self.teacher,
        )
        self.experiment = Experiment.objects.create(
            name="已有实验",
            description="已有实验说明",
            course=self.course,
            question_bank=self.bank,
            owner=self.teacher,
            confidence_randomization_seed="must-not-leak",
            heartbeat_interval_seconds=45,
        )

    def api_url(self, suffix=""):
        return f"/api/v1/teacher/courses/{self.course.pk}/experiments/{suffix}"

    def test_list_is_paginated_and_excludes_system_managed_fields(self):
        self.client.force_login(self.teacher)

        response = self.client.get(self.api_url())

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["page"]["size"], 20)
        self.assertEqual(payload["page"]["total_items"], 1)
        self.assertEqual(
            payload["summary"]["question_banks"],
            [{"id": self.bank.pk, "name": "实验 API 题库"}],
        )
        item = payload["items"][0]
        self.assertEqual(item["name"], "已有实验")
        self.assertEqual(
            item["question_bank"],
            {"id": self.bank.pk, "name": "实验 API 题库"},
        )
        for hidden_field in (
            "confidence_randomization_seed",
            "confidence_assignment_version",
            "confidence_scale_version",
            "confidence_prompt_version",
            "heartbeat_interval_seconds",
        ):
            self.assertNotIn(hidden_field, item)

    def test_detail_returns_teacher_decisions_and_quota_inventory(self):
        self.client.force_login(self.teacher)

        response = self.client.get(self.api_url(f"{self.experiment.pk}/"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["id"], self.experiment.pk)
        self.assertEqual(payload["selection_mode"], Experiment.SelectionMode.ALL)
        self.assertEqual(len(payload["question_quotas"]), 6)
        self.assertTrue(payload["can_edit"])
        self.assertNotIn("heartbeat_interval_seconds", payload)

    def test_inaccessible_course_is_not_disclosed(self):
        self.client.force_login(self.other_teacher)

        response = self.client.get(self.api_url())

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "NOT_FOUND")

    def test_student_role_is_rejected_before_course_lookup(self):
        self.client.force_login(self.student)

        response = self.client.get(self.api_url())

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "PERMISSION_DENIED")

    def test_create_accepts_only_teacher_managed_definition_fields(self):
        concept = Concept.objects.create(course=self.course, code="KC1", name="抽题范围")
        self.client.force_login(self.teacher)
        payload = {
            "name": "新建总题数实验",
            "description": "通过 API 新建",
            "question_bank_id": self.bank.pk,
            "sampling": {
                "mode": Experiment.SamplingMode.TOTAL_COUNT,
                "total_count": 12,
                "match_scope": Experiment.MatchScope.ANY_LEVEL,
                "nodes": [
                    {
                        "kc_code": concept.code,
                        "quota": None,
                        "match_scope": Experiment.MatchScope.ANY_LEVEL,
                    }
                ],
            },
            "ordering_mode": Experiment.Ordering.RANDOM,
            "feedback_mode": Experiment.Feedback.CORRECTNESS,
            "time_mode": Experiment.TimeMode.TOTAL,
            "total_time_limit_minutes": 30,
            "per_question_time_limit_seconds": None,
            "resume_mode": Experiment.ResumeMode.RESUMABLE,
            "confidence_mode": Experiment.ConfidenceMode.SAMPLED,
            "confidence_sampling_unit": Experiment.ConfidenceSamplingUnit.STUDENT,
            "confidence_probability": "0.250",
        }

        response = self.client.post(
            self.api_url(),
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        created = Experiment.objects.get(pk=response.json()["id"])
        self.assertEqual(created.course, self.course)
        self.assertEqual(created.owner, self.teacher)
        self.assertEqual(created.heartbeat_interval_seconds, 30)
        self.assertEqual(
            list(
                created.sampling_kcs.values_list(
                    "concept__code", "quota", "match_scope"
                )
            ),
            [(concept.code, None, Experiment.MatchScope.ANY_LEVEL)],
        )
        self.assertEqual(
            response.json()["sampling"]["nodes"][0]["kc_code"], concept.code
        )
        self.assertEqual(
            created.cross_experiment_item_policy,
            Experiment.CrossExperimentItemPolicy.EXCLUDE_PREVIOUS,
        )
        self.assertEqual(created.review_count, 0)

    def test_create_validates_mixed_review_count_against_assignment_size(self):
        concept = Concept.objects.create(course=self.course, code="KC3", name="复测范围")
        self.client.force_login(self.teacher)
        base = {
            "name": "混合复测实验",
            "question_bank_id": self.bank.pk,
            "sampling": {
                "mode": Experiment.SamplingMode.TOTAL_COUNT,
                "total_count": 4,
                "match_scope": Experiment.MatchScope.ANY_LEVEL,
                "nodes": [{
                    "kc_code": concept.code,
                    "quota": None,
                    "match_scope": Experiment.MatchScope.ANY_LEVEL,
                }],
            },
            "cross_experiment_item_policy": "mixed_review",
        }

        for review_count in (0, 4, 5):
            with self.subTest(review_count=review_count):
                response = self.client.post(
                    self.api_url(),
                    data=json.dumps({**base, "review_count": review_count}),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("review_count", response.json()["error"]["fields"])

        response = self.client.post(
            self.api_url(),
            data=json.dumps({**base, "review_count": 2}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["cross_experiment_item_policy"], "mixed_review")
        self.assertEqual(response.json()["review_count"], 2)

    def test_non_mixed_policy_rejects_nonzero_review_count(self):
        concept = Concept.objects.create(course=self.course, code="KC4", name="非复测范围")
        self.client.force_login(self.teacher)
        response = self.client.post(
            self.api_url(),
            data=json.dumps({
                "name": "排重实验",
                "question_bank_id": self.bank.pk,
                "sampling": {
                    "mode": Experiment.SamplingMode.TOTAL_COUNT,
                    "total_count": 2,
                    "match_scope": Experiment.MatchScope.ANY_LEVEL,
                    "nodes": [{
                        "kc_code": concept.code,
                        "quota": None,
                        "match_scope": Experiment.MatchScope.ANY_LEVEL,
                    }],
                },
                "cross_experiment_item_policy": "exclude_previous",
                "review_count": 1,
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("review_count", response.json()["error"]["fields"])

    def test_update_validates_mixed_review_against_submitted_kc_quotas(self):
        old_concept = Concept.objects.create(
            course=self.course,
            code="KC2",
            name="旧总题数范围",
        )
        new_concept = Concept.objects.create(
            course=self.course,
            code="KC3",
            name="新知识点配额范围",
        )
        self.experiment.sampling_mode = Experiment.SamplingMode.TOTAL_COUNT
        self.experiment.sampling_total_count = 2
        self.experiment.save(update_fields=["sampling_mode", "sampling_total_count"])
        ExperimentSamplingKC.objects.create(
            experiment=self.experiment,
            concept=old_concept,
            quota=None,
        )
        self.client.force_login(self.teacher)

        response = self.client.patch(
            self.api_url(f"{self.experiment.pk}/"),
            data=json.dumps(
                {
                    "sampling": {
                        "mode": Experiment.SamplingMode.KC_QUOTA,
                        "total_count": None,
                        "match_scope": Experiment.MatchScope.ANY_LEVEL,
                        "nodes": [
                            {
                                "kc_code": new_concept.code,
                                "quota": 4,
                                "match_scope": Experiment.MatchScope.ANY_LEVEL,
                            }
                        ],
                    },
                    "cross_experiment_item_policy": "mixed_review",
                    "review_count": 3,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.review_count, 3)
        self.assertEqual(
            list(self.experiment.sampling_kcs.values_list("concept_id", "quota")),
            [(new_concept.pk, 4)],
        )

        changed_quota = self.client.patch(
            self.api_url(f"{self.experiment.pk}/"),
            data=json.dumps(
                {
                    "sampling": {
                        "mode": Experiment.SamplingMode.KC_QUOTA,
                        "total_count": None,
                        "match_scope": Experiment.MatchScope.ANY_LEVEL,
                        "nodes": [
                            {
                                "kc_code": new_concept.code,
                                "quota": 6,
                                "match_scope": Experiment.MatchScope.ANY_LEVEL,
                            }
                        ],
                    },
                    "review_count": 5,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(changed_quota.status_code, 200, changed_quota.content)
        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.review_count, 5)
        self.assertEqual(self.experiment.sampling_kcs.get().quota, 6)

    def test_create_persists_total_count_question_type_quotas(self):
        concept = Concept.objects.create(course=self.course, code="KC101", name="题型配额范围")
        self.client.force_login(self.teacher)

        response = self.client.post(
            self.api_url(),
            data=json.dumps(
                {
                    "name": "题型构成实验",
                    "question_bank_id": self.bank.pk,
                    "sampling": {
                        "mode": Experiment.SamplingMode.TOTAL_COUNT,
                        "total_count": 3,
                        "match_scope": Experiment.MatchScope.ANY_LEVEL,
                        "nodes": [
                            {
                                "kc_code": concept.code,
                                "quota": None,
                                "match_scope": Experiment.MatchScope.ANY_LEVEL,
                            }
                        ],
                        "type_quotas": [
                            {"question_type": Question.Type.SINGLE, "amount": 2},
                            {"question_type": Question.Type.MULTIPLE, "amount": 1},
                        ],
                    },
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        experiment = Experiment.objects.get(pk=response.json()["id"])
        self.assertEqual(
            list(
                experiment.question_quotas.filter(amount__gt=0)
                .order_by("question_type")
                .values_list("question_type", "amount")
            ),
            [(Question.Type.MULTIPLE, 1), (Question.Type.SINGLE, 2)],
        )
        self.assertEqual(
            response.json()["sampling"]["type_quotas"],
            [
                {"question_type": Question.Type.SINGLE, "amount": 2},
                {"question_type": Question.Type.MULTIPLE, "amount": 1},
            ],
        )

    def test_create_rejects_invalid_total_count_question_type_quotas(self):
        concept = Concept.objects.create(course=self.course, code="KC102", name="无效题型范围")
        self.client.force_login(self.teacher)
        invalid_cases = {
            "duplicate": [
                {"question_type": Question.Type.SINGLE, "amount": 1},
                {"question_type": Question.Type.SINGLE, "amount": 1},
            ],
            "zero": [{"question_type": Question.Type.SINGLE, "amount": 0}],
            "sum_mismatch": [
                {"question_type": Question.Type.SINGLE, "amount": 1}
            ],
        }

        for suffix, type_quotas in invalid_cases.items():
            with self.subTest(case=suffix):
                response = self.client.post(
                    self.api_url(),
                    data=json.dumps(
                        {
                            "name": f"无效题型配额-{suffix}",
                            "question_bank_id": self.bank.pk,
                            "sampling": {
                                "mode": Experiment.SamplingMode.TOTAL_COUNT,
                                "total_count": 2,
                                "match_scope": Experiment.MatchScope.ANY_LEVEL,
                                "nodes": [
                                    {
                                        "kc_code": concept.code,
                                        "quota": None,
                                        "match_scope": Experiment.MatchScope.ANY_LEVEL,
                                    }
                                ],
                                "type_quotas": type_quotas,
                            },
                        }
                    ),
                    content_type="application/json",
                )

                self.assertEqual(response.status_code, 400, response.content)

    def test_kc_quota_mode_rejects_question_type_quotas(self):
        concept = Concept.objects.create(course=self.course, code="KC103", name="KC 配额范围")
        self.client.force_login(self.teacher)

        response = self.client.post(
            self.api_url(),
            data=json.dumps(
                {
                    "name": "不得组合二维配额",
                    "question_bank_id": self.bank.pk,
                    "sampling": {
                        "mode": Experiment.SamplingMode.KC_QUOTA,
                        "total_count": None,
                        "match_scope": Experiment.MatchScope.ANY_LEVEL,
                        "nodes": [
                            {
                                "kc_code": concept.code,
                                "quota": 1,
                                "match_scope": Experiment.MatchScope.ANY_LEVEL,
                            }
                        ],
                        "type_quotas": [
                            {"question_type": Question.Type.SINGLE, "amount": 1}
                        ],
                    },
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400, response.content)

    def test_switching_to_kc_quota_without_type_field_clears_saved_type_quotas(self):
        concept = Concept.objects.create(course=self.course, code="KC106", name="切换范围")
        self.experiment.sampling_mode = Experiment.SamplingMode.TOTAL_COUNT
        self.experiment.sampling_total_count = 1
        self.experiment.save(update_fields=["sampling_mode", "sampling_total_count"])
        ExperimentSamplingKC.objects.create(experiment=self.experiment, concept=concept)
        self.experiment.question_quotas.filter(
            question_type=Question.Type.SINGLE
        ).update(amount=1)
        self.client.force_login(self.teacher)

        response = self.client.patch(
            self.api_url(f"{self.experiment.pk}/"),
            data=json.dumps(
                {
                    "sampling": {
                        "mode": Experiment.SamplingMode.KC_QUOTA,
                        "total_count": None,
                        "match_scope": Experiment.MatchScope.ANY_LEVEL,
                        "nodes": [
                            {
                                "kc_code": concept.code,
                                "quota": 1,
                                "match_scope": Experiment.MatchScope.ANY_LEVEL,
                            }
                        ],
                    }
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(self.experiment.question_quotas.filter(amount__gt=0).exists())

    def test_sampling_inventory_counts_deduplicated_candidates_by_question_type(self):
        selected = Concept.objects.create(course=self.course, code="KC104", name="库存范围")
        other = Concept.objects.create(course=self.course, code="KC105", name="其他范围")
        for sequence, question_type, concept in (
            (1, Question.Type.SINGLE, selected),
            (2, Question.Type.MULTIPLE, selected),
            (3, Question.Type.SHORT, other),
        ):
            question = Question.objects.create(
                bank=self.bank,
                sequence=sequence,
                question_type=question_type,
                text=f"库存题 {sequence}",
                correct_answer="A" if question_type != Question.Type.MULTIPLE else ["A", "B"],
                status=Question.Status.ACTIVE,
            )
            QuestionConcept.objects.create(
                question=question,
                concept=concept,
                is_primary=True,
            )
        self.client.force_login(self.teacher)

        response = self.client.post(
            self.api_url("sampling-inventory/"),
            data=json.dumps(
                {
                    "question_bank_id": self.bank.pk,
                    "kc_codes": [selected.code],
                    "match_scope": Experiment.MatchScope.ANY_LEVEL,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["candidate_count"], 2)
        inventory = {
            item["question_type"]: item["available"]
            for item in response.json()["question_types"]
        }
        self.assertEqual(inventory[Question.Type.SINGLE], 1)
        self.assertEqual(inventory[Question.Type.MULTIPLE], 1)
        self.assertEqual(inventory[Question.Type.SHORT], 0)

    def test_kc_inventory_matches_readiness_for_selected_bank_and_scope(self):
        concept = Concept.objects.create(course=self.course, code="KC1", name="库存 KC")
        root = Concept.objects.create(course=self.course, code="KC2", name="另一个根 KC")
        other_bank = QuestionBank.objects.create(
            course=self.course, owner=self.teacher, name="其他题库"
        )

        for sequence, bank, status, primary in (
            (1, self.bank, Question.Status.ACTIVE, True),
            (2, self.bank, Question.Status.ACTIVE, False),
            (3, self.bank, Question.Status.DRAFT, True),
            (4, self.bank, Question.Status.ARCHIVED, True),
            (1, other_bank, Question.Status.ACTIVE, True),
        ):
            question = Question.objects.create(
                bank=bank, sequence=sequence, status=status,
                question_type=Question.Type.SHORT, text="库存测试题", correct_answer="答案",
            )
            parent = None if primary else QuestionConcept.objects.create(
                question=question, concept=root, is_primary=True,
            )
            QuestionConcept.objects.create(
                question=question, concept=concept, is_primary=primary, parent_link=parent,
            )
        self.experiment.sampling_mode = Experiment.SamplingMode.KC_QUOTA
        self.experiment.save(update_fields=["sampling_mode"])
        rule = ExperimentSamplingKC.objects.create(
            experiment=self.experiment, concept=concept, quota=1,
        )
        self.client.force_login(self.teacher)
        for scope, expected in ((Experiment.MatchScope.PRIMARY, 1), (Experiment.MatchScope.ANY_LEVEL, 2)):
            with self.subTest(scope=scope):
                rule.match_scope = scope
                rule.save(update_fields=["match_scope"])
                response = self.client.post(
                    self.api_url("sampling-inventory/"),
                    data=json.dumps({"question_bank_id": self.bank.pk, "kc_codes": [concept.code], "match_scope": scope}),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 200, response.content)
                inventory = {item["kc_code"]: item for item in response.json()["kc_counts"]}
                self.assertEqual(inventory[concept.code], {
                    "kc_code": concept.code, "primary_available": 1, "any_level_available": 2,
                })
                readiness = self.client.get(self.api_url(f"{self.experiment.pk}/readiness/")).json()
                self.assertTrue(readiness["ready"], readiness["issues"])
                self.assertEqual(response.json()["candidate_count"], expected)
                self.assertEqual(readiness["inventory_buckets"][0]["available"], expected)

        response = self.client.post(
            self.api_url("sampling-inventory/"),
            data=json.dumps({"question_bank_id": self.bank.pk, "kc_codes": [], "match_scope": "any_level"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["candidate_count"], 2)
        self.assertEqual(
            next(
                item["available"]
                for item in response.json()["question_types"]
                if item["question_type"] == Question.Type.SHORT
            ),
            2,
        )
        self.assertEqual(len(response.json()["kc_counts"]), 2)

    def test_create_total_count_without_kcs_uses_the_entire_bank(self):
        self.client.force_login(self.teacher)

        response = self.client.post(
            self.api_url(),
            data=json.dumps(
                {
                    "name": "全题库题型构成实验",
                    "question_bank_id": self.bank.pk,
                    "sampling": {
                        "mode": Experiment.SamplingMode.TOTAL_COUNT,
                        "total_count": 2,
                        "match_scope": Experiment.MatchScope.ANY_LEVEL,
                        "nodes": [],
                        "type_quotas": [
                            {"question_type": Question.Type.SINGLE, "amount": 1},
                            {"question_type": Question.Type.MULTIPLE, "amount": 1},
                        ],
                    },
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        experiment = Experiment.objects.get(pk=response.json()["id"])
        self.assertFalse(experiment.sampling_kcs.exists())
        self.assertEqual(response.json()["sampling"]["nodes"], [])

    def test_create_rejects_legacy_question_type_sampling_fields(self):
        self.client.force_login(self.teacher)

        response = self.client.post(
            self.api_url(),
            data=json.dumps(
                {
                    "name": "不得新建的旧抽题实验",
                    "question_bank_id": self.bank.pk,
                    "selection_mode": Experiment.SelectionMode.QUOTA,
                    "question_quotas": [
                        {"question_type": Question.Type.SINGLE, "amount": 1},
                    ],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("selection_mode", response.json()["error"]["fields"])
        self.assertFalse(Experiment.objects.filter(name="不得新建的旧抽题实验").exists())

    def test_patch_rejects_frozen_definition_with_conflict_code(self):
        self.experiment.status = Experiment.Status.PUBLISHED
        self.experiment.save(update_fields=["status"])
        self.client.force_login(self.teacher)

        response = self.client.patch(
            self.api_url(f"{self.experiment.pk}/"),
            data=json.dumps({"name": "不得改名"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "EXPERIMENT_FROZEN")
        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.name, "已有实验")

    def test_legacy_draft_patch_requires_complete_explicit_conversion(self):
        concept = Concept.objects.create(course=self.course, code="KC1", name="转换范围")
        self.client.force_login(self.teacher)

        rejected = self.client.patch(
            self.api_url(f"{self.experiment.pk}/"),
            data=json.dumps({"name": "不得偷偷改名"}),
            content_type="application/json",
        )

        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(
            rejected.json()["error"]["code"], "LEGACY_CONVERSION_REQUIRED"
        )
        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.name, "已有实验")

        converted = self.client.patch(
            self.api_url(f"{self.experiment.pk}/"),
            data=json.dumps(
                {
                    "sampling": {
                        "mode": Experiment.SamplingMode.TOTAL_COUNT,
                        "total_count": 1,
                        "match_scope": Experiment.MatchScope.ANY_LEVEL,
                        "nodes": [{"kc_code": concept.code, "quota": None, "match_scope": Experiment.MatchScope.ANY_LEVEL}],
                    },
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(converted.status_code, 200, converted.content)
        self.experiment.refresh_from_db()
        self.assertEqual(
            self.experiment.sampling_mode, Experiment.SamplingMode.TOTAL_COUNT
        )
        self.assertEqual(
            list(self.experiment.sampling_kcs.values_list("concept_id", "quota")),
            [(concept.pk, None)],
        )

    def test_readiness_reports_definition_failures_without_exposing_internals(self):
        self.client.force_login(self.teacher)

        response = self.client.get(
            self.api_url(f"{self.experiment.pk}/readiness/")
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["ready"])
        self.assertIn("题库中至少需要一道可用题目", response.json()["issues"])
        self.assertEqual(response.json()["candidate_count"], 0)
        self.assertNotIn("seed", response.json())

    def test_definition_readiness_does_not_require_a_preassigned_student(self):
        concept = Concept.objects.create(course=self.course, code="KC1", name="定义就绪 KC")
        question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="定义就绪题目",
            correct_answer="答案",
        )
        QuestionConcept.objects.create(
            question=question,
            concept=concept,
            is_primary=True,
        )
        self.experiment.sampling_mode = Experiment.SamplingMode.TOTAL_COUNT
        self.experiment.sampling_total_count = 1
        self.experiment.save(
            update_fields=["sampling_mode", "sampling_total_count"]
        )
        ExperimentSamplingKC.objects.create(
            experiment=self.experiment,
            concept=concept,
        )
        self.client.force_login(self.teacher)

        response = self.client.get(
            self.api_url(f"{self.experiment.pk}/readiness/")
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["ready"])
        self.assertEqual(response.json()["candidate_count"], 1)


class TeacherPublishPlanApiTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="publish-api-teacher",
            password="test-password",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            name="发布 API 课程",
            code="PUB-API",
            term="2026-Autumn",
            owner=self.teacher,
        )
        self.class_a = ClassGroup.objects.create(
            course=self.course,
            name="一班",
            academic_year="2026",
        )
        self.class_b = ClassGroup.objects.create(
            course=self.course,
            name="二班",
            academic_year="2026",
        )
        for index, class_group in enumerate((self.class_a, self.class_b), start=1):
            student = User.objects.create_user(
                username=f"PUB-{index:03d}",
                student_number=f"PUB-{index:03d}",
                role=User.Role.STUDENT,
            )
            CourseEnrollment.objects.create(
                course=self.course,
                class_group=class_group,
                student=student,
            )
        bank = QuestionBank.objects.create(
            name="发布 API 题库",
            course=self.course,
            owner=self.teacher,
        )
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            code="PUB-Q1",
            question_type=Question.Type.SHORT,
            text="发布 API 题目",
            correct_answer="答案",
        )
        concept = Concept.objects.create(course=self.course, code="KC1", name="发布 KC")
        QuestionConcept.objects.create(
            question=question,
            concept=concept,
            is_primary=True,
        )
        self.experiment = Experiment.objects.create(
            name="待发布实验",
            course=self.course,
            question_bank=bank,
            owner=self.teacher,
            created_by=self.teacher,
            sampling_mode=Experiment.SamplingMode.TOTAL_COUNT,
            sampling_total_count=1,
        )
        ExperimentSamplingKC.objects.create(
            experiment=self.experiment,
            concept=concept,
        )
        self.client.force_login(self.teacher)

    def start_url(self):
        return (
            f"/api/v1/teacher/courses/{self.course.pk}/experiments/"
            f"{self.experiment.pk}/publish-plans/"
        )

    def plan_url(self, plan, suffix=""):
        return f"/api/v1/teacher/publish-plans/{plan.pk}/{suffix}"

    def collection_url(self):
        return (
            f"/api/v1/teacher/experiments/{self.experiment.pk}/"
            "collection-participants/"
        )

    def collection_fixture(self, extra_student_count=0, extra_session_count=0):
        status_students = []
        status_definitions = (
            ("PUB-STATE-IN", "状态-进行中", ExperimentSession.Status.IN_PROGRESS, 0),
            ("PUB-STATE-COM", "状态-已完成", ExperimentSession.Status.COMPLETED, 3),
            ("PUB-STATE-EXP", "状态-已超时", ExperimentSession.Status.EXPIRED, 1),
            ("PUB-STATE-INV", "状态-已作废", ExperimentSession.Status.INVALIDATED, 2),
        )
        for student_number, display_name, status, submission_count in status_definitions:
            student = User.objects.create_user(
                username=student_number,
                student_number=student_number,
                display_name=display_name,
                role=User.Role.STUDENT,
            )
            CourseEnrollment.objects.create(
                course=self.course,
                class_group=self.class_a,
                student=student,
            )
            status_students.append((student, status, submission_count))
        for index in range(extra_student_count):
            student_number = f"PUB-EXTRA-{index + 1:03d}"
            student = User.objects.create_user(
                username=student_number,
                student_number=student_number,
                display_name=f"分页学生-{index + 1:03d}",
                role=User.Role.STUDENT,
            )
            CourseEnrollment.objects.create(
                course=self.course,
                class_group=self.class_a,
                student=student,
            )

        plan, _opens_at = self.preview_two_classes()
        commit = self.client.post(
            self.plan_url(plan, "commit/"),
            data=json.dumps({"expected_revision": plan.plan_revision}),
            content_type="application/json",
        )
        self.complete_operation(commit)

        version = self.experiment.versions.get()
        tasks = {
            task.class_group_id: task
            for task in ClassExperimentTask.objects.filter(experiment=self.experiment)
        }
        snapshots = {
            snapshot.student_id: snapshot
            for snapshot in version.participant_snapshots.select_related(
                "student", "class_task", "class_snapshot"
            )
        }
        for student, status, submission_count in status_students:
            snapshot = snapshots[student.pk]
            task = tasks[self.class_a.pk]
            ExperimentSession.objects.create(
                version=version,
                class_task=task,
                participant_snapshot=snapshot,
                student=student,
                class_snapshot=snapshot.class_snapshot,
                class_snapshot_name=snapshot.class_snapshot.name_snapshot,
                status=status,
                submission_count=submission_count,
            )
        extra_statuses = (
            ExperimentSession.Status.IN_PROGRESS,
            ExperimentSession.Status.COMPLETED,
            ExperimentSession.Status.EXPIRED,
            ExperimentSession.Status.INVALIDATED,
        )
        extra_students = [
            snapshot.student
            for snapshot in version.participant_snapshots.select_related("student")
            if snapshot.student.student_number.startswith("PUB-EXTRA-")
        ]
        for index, student in enumerate(extra_students[:extra_session_count]):
            snapshot = snapshots[student.pk]
            ExperimentSession.objects.create(
                version=version,
                class_task=tasks[self.class_a.pk],
                participant_snapshot=snapshot,
                student=student,
                class_snapshot=snapshot.class_snapshot,
                class_snapshot_name=snapshot.class_snapshot.name_snapshot,
                status=extra_statuses[index % len(extra_statuses)],
                submission_count=index % 4,
            )
        return {
            "version": version,
            "tasks": tasks,
            "snapshots": snapshots,
            "status_students": status_students,
        }

    def publish_other_experiment_for_collection_boundary(self):
        primary_experiment = self.experiment
        concept = primary_experiment.sampling_kcs.get().concept
        other_experiment = Experiment.objects.create(
            name="同课程另一实验",
            course=self.course,
            question_bank=primary_experiment.question_bank,
            owner=self.teacher,
            created_by=self.teacher,
            sampling_mode=Experiment.SamplingMode.TOTAL_COUNT,
            sampling_total_count=1,
        )
        ExperimentSamplingKC.objects.create(
            experiment=other_experiment,
            concept=concept,
        )
        other_class_a = ClassGroup.objects.create(
            course=self.course,
            name="另一实验一班",
            academic_year="2026",
        )
        other_class_b = ClassGroup.objects.create(
            course=self.course,
            name="另一实验二班",
            academic_year="2026",
        )
        for index, class_group in enumerate((other_class_a, other_class_b), start=1):
            student_number = f"PUB-OTHER-{index:03d}"
            student = User.objects.create_user(
                username=student_number,
                student_number=student_number,
                display_name=f"另一实验学生-{index}",
                role=User.Role.STUDENT,
            )
            CourseEnrollment.objects.create(
                course=self.course,
                class_group=class_group,
                student=student,
            )
        self.experiment = other_experiment
        primary_class_a = self.class_a
        primary_class_b = self.class_b
        self.class_a = other_class_a
        self.class_b = other_class_b
        try:
            plan, _opens_at = self.preview_two_classes()
            commit = self.client.post(
                self.plan_url(plan, "commit/"),
                data=json.dumps({"expected_revision": plan.plan_revision}),
                content_type="application/json",
            )
            self.complete_operation(commit)
            snapshots = list(
                other_experiment.versions.get().participant_snapshots.values_list(
                    "pk", "student_id"
                )
            )
        finally:
            self.experiment = primary_experiment
            self.class_a = primary_class_a
            self.class_b = primary_class_b
        return snapshots

    def test_collection_participants_exposes_all_server_derived_statuses_and_choices(self):
        fixture = self.collection_fixture()

        response = self.client.get(self.collection_url())

        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(
            Counter(item["status"] for item in payload["items"]),
            Counter(
                {
                    "not_started": 2,
                    "in_progress": 1,
                    "completed": 1,
                    "expired": 1,
                    "invalidated": 1,
                }
            ),
        )
        choices = {choice["value"] for choice in payload["status_choices"]}
        self.assertEqual(
            choices,
            {"not_started", "in_progress", "completed", "expired", "invalidated"},
        )
        not_started = next(
            item for item in payload["items"] if item["status"] == "not_started"
        )
        self.assertIsNone(not_started["started_at"])
        self.assertEqual(not_started["submission_count"], 0)
        self.assertEqual(
            {item["class_id"] for item in payload["items"]},
            {self.class_a.pk, self.class_b.pk},
        )
        self.assertEqual(fixture["version"].participant_snapshots.count(), 6)

    def test_collection_participants_filters_by_class_status_and_student_number(self):
        fixture = self.collection_fixture()
        student, _status, _submission_count = fixture["status_students"][0]

        class_filtered = self.client.get(
            self.collection_url(), {"class_id": self.class_b.pk}
        )
        status_filtered = self.client.get(
            self.collection_url(), {"status": "in_progress"}
        )
        response = self.client.get(
            self.collection_url(),
            {"class_id": self.class_a.pk, "status": "in_progress", "q": student.student_number},
        )
        wrong_class = self.client.get(
            self.collection_url(),
            {"class_id": self.class_b.pk, "q": student.student_number},
        )
        wrong_status = self.client.get(
            self.collection_url(),
            {"status": "completed", "q": student.student_number},
        )

        self.assertEqual(class_filtered.status_code, 200, class_filtered.content)
        self.assertEqual(status_filtered.status_code, 200, status_filtered.content)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(wrong_class.status_code, 200, wrong_class.content)
        self.assertEqual(wrong_status.status_code, 200, wrong_status.content)
        self.assertEqual(
            {item["class_id"] for item in class_filtered.json()["items"]},
            {self.class_b.pk},
        )
        self.assertTrue(
            all(item["status"] == "in_progress" for item in status_filtered.json()["items"])
        )
        self.assertEqual(response.json()["page"]["total_items"], 1)
        self.assertEqual(response.json()["items"][0]["student_number"], student.student_number)
        self.assertEqual(wrong_class.json()["page"]["total_items"], 0)
        self.assertEqual(wrong_status.json()["page"]["total_items"], 0)

    def test_collection_participants_searches_student_name(self):
        fixture = self.collection_fixture()
        student, _status, _submission_count = fixture["status_students"][1]

        response = self.client.get(self.collection_url(), {"q": student.display_name})

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["page"]["total_items"], 1)
        self.assertEqual(response.json()["items"][0]["display_name"], student.display_name)

    def test_collection_participants_searches_composed_full_name(self):
        student = User.objects.create_user(
            username="PUB-FULL-NAME",
            student_number="PUB-FULL-NAME",
            display_name="",
            first_name="Jane",
            last_name="Doe",
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_a,
            student=student,
        )
        self.collection_fixture()

        response = self.client.get(self.collection_url(), {"q": "Jane Doe"})

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["page"]["total_items"], 1)
        self.assertEqual(response.json()["items"][0]["display_name"], student.shown_name)

    def test_collection_participants_rejects_invalid_filters_and_unauthorized_access(self):
        self.collection_fixture()
        unpublished_class = ClassGroup.objects.create(
            course=self.course,
            name="未发布班",
            academic_year="2026",
        )

        invalid_status = self.client.get(self.collection_url(), {"status": "unknown"})
        self.assertEqual(invalid_status.status_code, 400, invalid_status.content)
        invalid_class = self.client.get(
            self.collection_url(), {"class_id": unpublished_class.pk}
        )
        self.assertEqual(invalid_class.status_code, 400, invalid_class.content)
        invalid_page = self.client.get(self.collection_url(), {"page": 0})
        self.assertEqual(invalid_page.status_code, 400, invalid_page.content)

        other_teacher = User.objects.create_user(
            username="publish-api-other-teacher",
            password="test-password",
            role=User.Role.TEACHER,
        )
        self.client.force_login(other_teacher)
        forbidden = self.client.get(self.collection_url())
        self.assertEqual(forbidden.status_code, 404, forbidden.content)

    def test_collection_participants_does_not_leak_another_experiments_snapshots(self):
        primary_fixture = self.collection_fixture()
        other_snapshots = self.publish_other_experiment_for_collection_boundary()

        response = self.client.get(self.collection_url())

        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        participant_ids = {item["participant_id"] for item in payload["items"]}
        student_numbers = {item["student_number"] for item in payload["items"]}
        self.assertTrue(primary_fixture["snapshots"])
        self.assertTrue(
            {snapshot.pk for snapshot in primary_fixture["snapshots"].values()}
            & participant_ids
        )
        self.assertTrue(
            set(snapshot_id for snapshot_id, _student_id in other_snapshots).isdisjoint(
                participant_ids
            )
        )
        other_student_numbers = set(
            User.objects.filter(
                pk__in=[student_id for _snapshot_id, student_id in other_snapshots]
            ).values_list("student_number", flat=True)
        )
        self.assertTrue(other_student_numbers.isdisjoint(student_numbers))

    def test_collection_participants_ignores_session_from_another_class_task(self):
        fixture = self.collection_fixture()
        class_a_task = fixture["tasks"][self.class_a.pk]
        class_b_task = fixture["tasks"][self.class_b.pk]
        snapshot = next(
            snapshot
            for snapshot in fixture["snapshots"].values()
            if snapshot.class_task_id == class_a_task.pk
            and snapshot.student.student_number == "PUB-001"
        )
        ExperimentSession.objects.create(
            version=fixture["version"],
            class_task=class_b_task,
            participant_snapshot=snapshot,
            student=snapshot.student,
            class_snapshot=snapshot.class_snapshot,
            class_snapshot_name=snapshot.class_snapshot.name_snapshot,
            status=ExperimentSession.Status.IN_PROGRESS,
        )

        response = self.client.get(
            self.collection_url(), {"class_id": self.class_a.pk}
        )

        self.assertEqual(response.status_code, 200, response.content)
        participant = next(
            item
            for item in response.json()["items"]
            if item["participant_id"] == snapshot.pk
        )
        self.assertEqual(participant["status"], "not_started")

    def test_collection_participants_query_count_is_bounded_for_a_page_of_25(self):
        self.collection_fixture(extra_student_count=30, extra_session_count=30)

        with CaptureQueriesContext(connection) as small_context:
            small_response = self.client.get(
                self.collection_url(), {"class_id": self.class_b.pk}
            )
        with CaptureQueriesContext(connection) as large_context:
            response = self.client.get(self.collection_url(), {"page": 1})

        self.assertEqual(small_response.status_code, 200, small_response.content)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(response.json()["items"]), 25)
        self.assertEqual(len(small_context), len(large_context))
        self.assertLessEqual(len(large_context), 12)

    def test_collection_participants_pagination_is_stable_and_non_overlapping(self):
        fixture = self.collection_fixture(extra_student_count=30)

        first = self.client.get(self.collection_url(), {"page": 1})
        second = self.client.get(self.collection_url(), {"page": 2})
        repeated_first = self.client.get(self.collection_url(), {"page": 1})

        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(repeated_first.status_code, 200, repeated_first.content)
        first_ids = [item["participant_id"] for item in first.json()["items"]]
        second_ids = [item["participant_id"] for item in second.json()["items"]]
        repeated_first_ids = [
            item["participant_id"] for item in repeated_first.json()["items"]
        ]
        all_ids = first_ids + second_ids
        expected_ids = [snapshot.pk for snapshot in fixture["snapshots"].values()]
        self.assertEqual(len(first_ids), 25)
        self.assertEqual(first.json()["page"]["total_items"], 36)
        self.assertEqual(len(second_ids), 11)
        self.assertEqual(set(all_ids), set(expected_ids))
        self.assertEqual(len(all_ids), len(set(all_ids)))
        self.assertEqual(first_ids, repeated_first_ids)

    def start_plan(self):
        response = self.client.post(self.start_url())
        self.assertEqual(response.status_code, 201, response.content)
        return PublishPlan.objects.get(pk=response.json()["id"]), response.json()

    def put_setup(self, plan, payload):
        return self.client.put(
            self.plan_url(plan, "setup/"),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def complete_operation(self, response):
        self.assertEqual(response.status_code, 202, response.content)
        job = PublishOperationJob.objects.get(pk=response.json()["job_id"])
        run_publish_operation(job)
        job.refresh_from_db()
        self.assertEqual(job.status, PublishOperationJob.Status.SUCCEEDED)
        return job

    def test_start_and_get_plan_expose_resumable_setup_without_technical_seeds(self):
        plan, payload = self.start_plan()

        self.assertEqual(payload["setup_status"], "empty")
        self.assertEqual(payload["missing_requirements"], ["classes"])
        self.assertEqual(payload["plan_revision"], 1)
        self.assertNotIn("input_hash", payload)
        self.assertNotIn("algorithm_version", payload)

        resumed = self.client.post(self.start_url())
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(resumed.json()["id"], plan.pk)
        detail = self.client.get(self.plan_url(plan))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["id"], plan.pk)

    def test_selected_classes_have_a_valid_immediate_indefinite_default_schedule(self):
        plan, _ = self.start_plan()

        response = self.put_setup(
            plan,
            {
                "expected_revision": plan.plan_revision,
                "classes": [
                    {"class_id": self.class_a.pk, "excluded_student_ids": []},
                    {"class_id": self.class_b.pk, "excluded_student_ids": []},
                ],
                "default_schedule": {"opens_at": None, "closes_at": None},
                "schedules": [],
            },
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["setup_status"], "complete")
        self.assertEqual(response.json()["missing_requirements"], [])
        self.assertEqual(
            [row["schedule_mode"] for row in response.json()["classes"]],
            ["default", "default"],
        )
        self.assertTrue(
            all(
                row["opens_at"] is None and row["closes_at"] is None
                for row in response.json()["classes"]
            )
        )

    def test_plan_projects_a_specific_blocker_for_an_empty_selected_class(self):
        empty_class = ClassGroup.objects.create(
            course=self.course,
            name="空班",
            academic_year="2026",
        )
        plan, _ = self.start_plan()

        response = self.put_setup(
            plan,
            {
                "expected_revision": plan.plan_revision,
                "classes": [
                    {"class_id": empty_class.pk, "excluded_student_ids": []},
                ],
                "default_schedule": {"opens_at": None, "closes_at": None},
                "schedules": [],
            },
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["setup_status"], "complete")
        self.assertEqual(
            response.json()["blocking_issues"],
            [
                {
                    "code": "CLASS_NO_PARTICIPANTS",
                    "message": "班级“2026 空班”没有可发布参与者。",
                    "class_id": empty_class.pk,
                    "kc_id": None,
                    "field": "classes",
                }
            ],
        )

    def test_plan_projects_a_specific_blocker_for_insufficient_kc_inventory(self):
        concept = self.experiment.question_bank.questions.get().primary_concept
        self.experiment.sampling_mode = Experiment.SamplingMode.KC_QUOTA
        self.experiment.sampling_total_count = None
        self.experiment.save(
            update_fields=["sampling_mode", "sampling_total_count"]
        )
        self.experiment.sampling_kcs.update(
            quota=2,
        )
        readiness = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/experiments/{self.experiment.pk}/readiness/"
        )
        self.assertEqual(readiness.status_code, 200, readiness.content)
        self.assertEqual(readiness.json()["candidate_count"], 1)
        self.assertEqual(readiness.json()["requested_count"], 2)
        self.assertEqual(
            readiness.json()["inventory_buckets"],
            [
                {
                    "kc_code": concept.code,
                    "kc_name": concept.name,
                    "match_scope": Experiment.MatchScope.PRIMARY,
                    "requested": 2,
                    "available": 1,
                }
            ],
        )
        self.assertEqual(
            readiness.json()["blocking_issues"][0]["code"],
            "KC_QUOTA_INSUFFICIENT",
        )
        plan, _ = self.start_plan()

        response = self.put_setup(
            plan,
            {
                "expected_revision": 1,
                "classes": [
                    {"class_id": self.class_a.pk, "excluded_student_ids": []},
                ],
                "default_schedule": {"opens_at": None, "closes_at": None},
                "schedules": [],
            },
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            response.json()["blocking_issues"],
            [
                {
                    "code": "KC_QUOTA_INSUFFICIENT",
                    "message": f"知识点“{concept.name}”需要 2 题，可用 1 题。",
                    "class_id": None,
                    "kc_id": concept.pk,
                    "kc_code": concept.code,
                    "field": "sampling_kcs",
                }
            ],
        )

    def test_setup_treats_omitted_class_schedules_as_batch_defaults_and_reports_stale_revision(self):
        plan, _ = self.start_plan()
        response = self.put_setup(
            plan,
            {
                "expected_revision": 1,
                "classes": [
                    {"class_id": self.class_a.pk, "excluded_student_ids": []},
                    {"class_id": self.class_b.pk, "excluded_student_ids": []},
                ],
                "default_schedule": {"opens_at": None, "closes_at": None},
                "schedules": [
                    {"class_id": self.class_a.pk, "mode": "default"},
                ],
            },
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["setup_status"], "complete")
        self.assertEqual(response.json()["missing_requirements"], [])
        self.assertGreater(response.json()["plan_revision"], 1)
        plan.refresh_from_db()
        class_states = {
            item.class_group_id: item.use_batch_default
            for item in plan.class_plans.all()
        }
        self.assertEqual(
            class_states,
            {self.class_a.pk: True, self.class_b.pk: True},
        )

        stale = self.put_setup(
            plan,
            {
                "expected_revision": 1,
                "classes": [],
                "default_schedule": {"opens_at": None, "closes_at": None},
                "schedules": [],
            },
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["error"]["code"], "PUBLISH_PLAN_STALE")

    def test_preview_accepts_an_immediate_indefinite_schedule(self):
        plan, _ = self.start_plan()
        setup = self.put_setup(
            plan,
            {
                "expected_revision": 1,
                "classes": [
                    {"class_id": self.class_a.pk, "excluded_student_ids": []},
                ],
                "default_schedule": {"opens_at": None, "closes_at": None},
                "schedules": [],
            },
        )
        plan.refresh_from_db()

        response = self.client.post(
            self.plan_url(plan, "preview/"),
            data=json.dumps({"expected_revision": plan.plan_revision}),
            content_type="application/json",
        )

        self.assertEqual(setup.status_code, 200)
        self.complete_operation(response)
        plan.refresh_from_db()
        self.assertEqual(plan.status, PublishPlan.Status.PREVIEWED)
        self.assertEqual(
            list(plan.class_plans.values_list("class_group_id", flat=True)),
            [self.class_a.pk],
        )

    def test_preview_review_and_commit_publish_all_selected_classes_atomically(self):
        plan, _ = self.start_plan()
        opens_at = timezone.now() + timedelta(hours=1)
        setup = self.put_setup(
            plan,
            {
                "expected_revision": 1,
                "classes": [
                    {"class_id": self.class_a.pk, "excluded_student_ids": []},
                    {"class_id": self.class_b.pk, "excluded_student_ids": []},
                ],
                "default_schedule": {
                    "opens_at": opens_at.isoformat(),
                    "closes_at": None,
                },
                "schedules": [
                    {"class_id": self.class_a.pk, "mode": "default"},
                    {"class_id": self.class_b.pk, "mode": "default"},
                ],
            },
        )
        self.assertEqual(setup.status_code, 200, setup.content)
        plan.refresh_from_db()

        preview = self.client.post(
            self.plan_url(plan, "preview/"),
            data=json.dumps({"expected_revision": plan.plan_revision}),
            content_type="application/json",
        )
        self.complete_operation(preview)

        review = self.client.get(self.plan_url(plan, "review/"))
        self.assertEqual(review.status_code, 200)
        self.assertEqual(review.json()["preview"]["participant_count"], 2)
        self.assertEqual(review.json()["preview"]["class_count"], 2)
        self.assertEqual(review.json()["preview"]["randomization_validation"], "2/2")
        self.assertNotIn("output_hash", review.json()["preview"])
        self.assertIn("发布后实验定义不可修改", review.json()["effects"])

        plan.refresh_from_db()
        commit = self.client.post(
            self.plan_url(plan, "commit/"),
            data=json.dumps({"expected_revision": plan.plan_revision}),
            content_type="application/json",
        )
        self.complete_operation(commit)
        self.assertEqual(
            ClassExperimentTask.objects.filter(experiment=self.experiment).count(),
            2,
        )
        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.status, Experiment.Status.FROZEN)

    def preview_two_classes(self):
        plan, _ = self.start_plan()
        opens_at = timezone.now() + timedelta(hours=2)
        setup = self.put_setup(
            plan,
            {
                "expected_revision": plan.plan_revision,
                "classes": [
                    {"class_id": self.class_a.pk, "excluded_student_ids": []},
                    {"class_id": self.class_b.pk, "excluded_student_ids": []},
                ],
                "default_schedule": {
                    "opens_at": opens_at.isoformat(),
                    "closes_at": None,
                },
                "schedules": [
                    {"class_id": self.class_a.pk, "mode": "default"},
                    {"class_id": self.class_b.pk, "mode": "default"},
                ],
            },
        )
        self.assertEqual(setup.status_code, 200, setup.content)
        plan.refresh_from_db()
        preview = self.client.post(
            self.plan_url(plan, "preview/"),
            data=json.dumps({"expected_revision": plan.plan_revision}),
            content_type="application/json",
        )
        self.complete_operation(preview)
        plan.refresh_from_db()
        return plan, opens_at

    def test_draft_participants_expose_selected_class_roster_and_exclusions(self):
        excluded_student = User.objects.get(student_number="PUB-001")
        plan, _ = self.start_plan()
        setup = self.put_setup(
            plan,
            {
                "expected_revision": plan.plan_revision,
                "classes": [
                    {
                        "class_id": self.class_a.pk,
                        "excluded_student_ids": [excluded_student.pk],
                    },
                ],
                "default_schedule": {"opens_at": None, "closes_at": None},
                "schedules": [],
            },
        )
        self.assertEqual(setup.status_code, 200, setup.content)

        participants = self.client.get(self.plan_url(plan, "participants/"))

        self.assertEqual(participants.status_code, 200, participants.content)
        self.assertEqual(participants.json()["page"]["total_items"], 1)
        roster_entry = participants.json()["items"][0]
        self.assertEqual(roster_entry["id"], excluded_student.pk)
        self.assertTrue(roster_entry["excluded"])
        self.assertEqual(roster_entry["item_count"], 0)
        detail = self.client.get(
            self.plan_url(plan, f"participants/{excluded_student.pk}/")
        )
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertEqual(detail.json()["items"], [])
        self.assertTrue(detail.json()["excluded"])

    def test_preview_participants_are_paginated_inspectable_and_exportable(self):
        plan, _opens_at = self.preview_two_classes()

        participants = self.client.get(self.plan_url(plan, "participants/"))

        self.assertEqual(participants.status_code, 200)
        self.assertEqual(participants.json()["page"]["total_items"], 2)
        first = participants.json()["items"][0]
        self.assertEqual(first["item_count"], 1)
        self.assertFalse(first["excluded"])
        self.assertIn("student_number", first)
        self.assertNotIn("randomization_subject_key", first)
        self.assertNotIn("sampling_seed", first)
        self.assertNotIn("ordering_seed", first)

        detail = self.client.get(
            self.plan_url(plan, f"participants/{first['id']}/")
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["items"][0]["assigned_sequence_index"], 1)
        self.assertEqual(detail.json()["items"][0]["question_code"], "PUB-Q1")
        self.assertNotIn("question_release_fingerprint", detail.json()["items"][0])

        export = self.client.get(self.plan_url(plan, "participants/export/"))
        self.assertEqual(export.status_code, 200)
        self.assertEqual(export["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("student_number,class_name,item_count", export.content.decode("utf-8-sig"))
        self.assertEqual(len(export.content.decode("utf-8-sig").splitlines()), 3)

        commit = self.client.post(
            self.plan_url(plan, "commit/"),
            data=json.dumps({"expected_revision": plan.plan_revision}),
            content_type="application/json",
        )
        self.complete_operation(commit)
        Question.objects.filter(pk=detail.json()["items"][0]["question_id"]).update(
            code="MUTATED",
            question_type=Question.Type.TRUE_FALSE,
        )

        frozen_detail = self.client.get(
            self.plan_url(plan, f"participants/{first['id']}/")
        )
        self.assertEqual(frozen_detail.status_code, 200)
        self.assertEqual(frozen_detail.json()["items"][0]["question_code"], "PUB-Q1")
        self.assertNotEqual(
            frozen_detail.json()["items"][0]["question_type"],
            Question.Type.TRUE_FALSE,
        )

    def test_class_task_projection_progress_and_terminal_actions_use_server_state(self):
        plan, opens_at = self.preview_two_classes()
        commit = self.client.post(
            self.plan_url(plan, "commit/"),
            data=json.dumps({"expected_revision": plan.plan_revision}),
            content_type="application/json",
        )
        self.complete_operation(commit)

        task_list = self.client.get(
            f"/api/v1/teacher/experiments/{self.experiment.pk}/class-tasks/"
        )
        self.assertEqual(task_list.status_code, 200)
        self.assertEqual(len(task_list.json()["items"]), 2)
        task_id = task_list.json()["items"][0]["id"]

        detail = self.client.get(f"/api/v1/teacher/class-tasks/{task_id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["business_status"], "scheduled")
        self.assertEqual(
            detail.json()["available_actions"],
            ["grade_short_answers", "adjust_time", "revise_schedule", "cancel", "manual_close"],
        )

        progress = self.client.get(
            f"/api/v1/teacher/class-tasks/{task_id}/progress/"
        )
        self.assertEqual(progress.status_code, 200)
        self.assertEqual(progress.json()["total_participants"], 1)
        self.assertEqual(progress.json()["not_started"], 1)

        revised_opens = opens_at + timedelta(hours=1)
        revision = self.client.post(
            f"/api/v1/teacher/class-tasks/{task_id}/schedule-revisions/",
            data=json.dumps(
                {
                    "opens_at": revised_opens.isoformat(),
                    "closes_at": None,
                    "reason": "教学安排调整",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(revision.status_code, 201, revision.content)
        self.assertEqual(revision.json()["schedule_revision"], 2)

        cancelled = self.client.post(
            f"/api/v1/teacher/class-tasks/{task_id}/cancel/",
            data=json.dumps({"reason": "本班取消"}),
            content_type="application/json",
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["business_status"], "cancelled")
        self.assertEqual(cancelled.json()["available_actions"], ["grade_short_answers"])

        other_task_id = next(
            item["id"]
            for item in task_list.json()["items"]
            if item["id"] != task_id
        )
        closed = self.client.post(
            f"/api/v1/teacher/class-tasks/{other_task_id}/manual-close/",
            data=json.dumps({"reason": "提前结束"}),
            content_type="application/json",
        )
        self.assertEqual(closed.status_code, 200)
        self.assertEqual(closed.json()["business_status"], "manual_closed")

    def test_closed_task_can_extend_then_reopen_after_manual_close_without_erasing_history(self):
        plan, _ = self.preview_two_classes()
        commit = self.client.post(
            self.plan_url(plan, "commit/"),
            data=json.dumps({"expected_revision": plan.plan_revision}),
            content_type="application/json",
        )
        self.complete_operation(commit)
        task = ClassExperimentTask.objects.get(experiment=self.experiment, class_group=self.class_a)
        original = task.schedule_revisions.get(revision_no=1)
        now = timezone.now()
        task.schedule_revisions.filter(pk=original.pk).update(
            opens_at=now - timedelta(hours=1), closes_at=now - timedelta(minutes=1)
        )
        url = f"/api/v1/teacher/class-tasks/{task.pk}/schedule-revisions/"

        extended = self.client.post(
            url,
            data=json.dumps({"expected_revision": 1, "extend_minutes": 5, "reason": "课堂补时"}),
            content_type="application/json",
        )
        self.assertEqual(extended.status_code, 201, extended.content)
        self.assertEqual(extended.json()["schedule_revision"], 2)
        self.assertEqual(extended.json()["business_status"], "open")
        self.assertGreater(task.schedule_revisions.get(revision_no=2).closes_at, now + timedelta(minutes=4))

        stale = self.client.post(
            url,
            data=json.dumps({"expected_revision": 1, "extend_minutes": 5, "reason": "旧页面"}),
            content_type="application/json",
        )
        self.assertEqual(stale.status_code, 409, stale.content)
        self.assertEqual(task.schedule_revisions.count(), 2)

        closed = self.client.post(
            f"/api/v1/teacher/class-tasks/{task.pk}/manual-close/",
            data=json.dumps({"reason": "课堂暂停"}),
            content_type="application/json",
        )
        self.assertEqual(closed.status_code, 200, closed.content)
        manual_closed_at = ClassExperimentTask.objects.get(pk=task.pk).manual_closed_at
        reopened = self.client.post(
            url,
            data=json.dumps({
                "expected_revision": 2,
                "reopen": True,
                "opens_at": timezone.now().isoformat(),
                "closes_at": (timezone.now() + timedelta(minutes=10)).isoformat(),
                "reason": "恢复课堂",
            }),
            content_type="application/json",
        )
        self.assertEqual(reopened.status_code, 201, reopened.content)
        self.assertEqual(reopened.json()["business_status"], "open")
        task.refresh_from_db()
        self.assertEqual(task.manual_closed_at, manual_closed_at)
        self.assertEqual(task.schedule_revisions.count(), 3)
        closed_again = self.client.post(
            f"/api/v1/teacher/class-tasks/{task.pk}/manual-close/",
            data=json.dumps({"reason": "第二次暂停"}), content_type="application/json",
        )
        self.assertEqual(closed_again.status_code, 200, closed_again.content)
        self.assertEqual(closed_again.json()["business_status"], "manual_closed")
        self.assertEqual(task.manual_closures.count(), 2)
        self.assertEqual(ClassExperimentTask.objects.get(pk=task.pk).manual_closed_at, manual_closed_at)

    def test_legacy_manual_close_without_history_can_reopen(self):
        plan, _ = self.preview_two_classes()
        commit = self.client.post(
            self.plan_url(plan, "commit/"),
            data=json.dumps({"expected_revision": plan.plan_revision}),
            content_type="application/json",
        )
        self.complete_operation(commit)
        task = ClassExperimentTask.objects.get(experiment=self.experiment, class_group=self.class_a)
        task.schedule_revisions.filter(revision_no=1).update(
            opens_at=timezone.now() - timedelta(hours=1),
            closes_at=timezone.now() + timedelta(hours=1),
        )
        manual_close_task(task, self.teacher, reason="旧记录的提前结束")
        task.manual_closures.all().delete()  # Model a pre-migration task with no closure-history row.
        task.refresh_from_db()
        self.assertFalse(task.manual_closures.exists())
        self.assertEqual(DeadlineDecisionService.get_task_state(task), "manual_closed")

        revision = append_schedule_revision(
            task, self.teacher, expected_revision=1, reopen=True,
            closes_at=timezone.now() + timedelta(hours=1), reason="恢复旧任务",
        )

        self.assertTrue(revision.reopens_manual_close)
        self.assertEqual(DeadlineDecisionService.get_task_state(task), "open")
        self.assertFalse(task.manual_closures.exists())

    def test_expired_student_resumes_once_with_same_session_and_preserved_timeout_event(self):
        plan, _ = self.preview_two_classes()
        commit = self.client.post(
            self.plan_url(plan, "commit/"),
            data=json.dumps({"expected_revision": plan.plan_revision}),
            content_type="application/json",
        )
        self.complete_operation(commit)
        task = ClassExperimentTask.objects.get(experiment=self.experiment, class_group=self.class_a)
        task.schedule_revisions.filter(revision_no=1).update(
            opens_at=timezone.now() - timedelta(hours=1),
            closes_at=timezone.now() + timedelta(minutes=1),
        )
        student = User.objects.get(student_number="PUB-001")
        session = get_or_create_session(self.experiment, student, class_task=task)
        attempt = session.attempts.get()
        _terminate_for_deadline(session, now=timezone.now(), reason="deadline_expired")
        self.assertEqual(attempt.assessment_events.filter(event_type="attempt_closed").count(), 1)
        task.schedule_revisions.filter(revision_no=1).update(closes_at=timezone.now() - timedelta(seconds=1))
        received_while_closed = timezone.now()
        append_schedule_revision(
            task, self.teacher, expected_revision=1, extend_minutes=5, reason="恢复作答"
        )
        self.assertEqual(DeadlineDecisionService.get_task_state(task, at=received_while_closed), "closed")
        self.assertFalse(DeadlineDecisionService.can_enter_item(task, session, received_while_closed).allowed)
        accept_research_consent(student)
        self.client.force_login(student)
        student_list = self.client.get(f"/api/v1/student/courses/{self.course.pk}/tasks/")
        self.assertEqual(student_list.status_code, 200, student_list.content)
        self.assertEqual(student_list.json()["items"][0]["primary_action"], "resume")
        direct_refresh = self.client.get(
            f"/api/v1/student/sessions/{session.pk}/questions/{session.assignments.get().pk}/"
        )
        self.assertEqual(direct_refresh.status_code, 200, direct_refresh.content)
        self.assertGreater(direct_refresh.json()["remaining_seconds"], 0)
        resume_response = self.client.post(f"/api/v1/student/tasks/{task.pk}/resume/")
        self.assertEqual(resume_response.status_code, 200, resume_response.content)
        resumed = ExperimentSession.objects.get(pk=session.pk)
        repeated = resume_task_session(task, student)
        self.assertEqual(resumed.pk, session.pk)
        self.assertEqual(repeated.recovery_round, 1)
        self.assertEqual(resumed.status, ExperimentSession.Status.IN_PROGRESS)
        self.assertEqual(AssessmentEvent.objects.filter(session=session, event_type="session_expired").count(), 1)
        self.assertEqual(AssessmentEvent.objects.filter(session=session, event_type="session_reopened").count(), 1)
        attempt.refresh_from_db()
        self.assertNotEqual(attempt.status, Attempt.Status.UNANSWERED)
        _terminate_for_deadline(resumed, now=timezone.now(), reason="deadline_expired")
        self.assertEqual(AssessmentEvent.objects.filter(session=session, event_type="session_expired").count(), 2)
        self.assertEqual(AssessmentEvent.objects.filter(attempt=attempt, event_type="attempt_closed").count(), 2)
        coverage_run = generate_session_coverage(resumed)
        self.assertEqual(
            coverage_run.session_results.get(session=session).analysis_eligibility["supplemental_time"]["recovery_rounds"],
            1,
        )
        self.assertEqual(
            coverage_run.attempt_results.get(attempt=attempt).derived_features["supplemental_recovery_rounds"],
            [1],
        )

    def test_total_limit_extension_grants_only_added_time_from_first_resume(self):
        self.experiment.time_mode = Experiment.TimeMode.TOTAL
        self.experiment.total_time_limit_minutes = 1
        self.experiment.save()
        plan, _ = self.preview_two_classes()
        commit = self.client.post(self.plan_url(plan, "commit/"), data=json.dumps({"expected_revision": plan.plan_revision}), content_type="application/json")
        self.complete_operation(commit)
        task = ClassExperimentTask.objects.get(experiment=self.experiment, class_group=self.class_a)
        task.schedule_revisions.filter(revision_no=1).update(opens_at=timezone.now() - timedelta(hours=1))
        student = User.objects.get(student_number="PUB-001")
        session = get_or_create_session(self.experiment, student, class_task=task)
        old_deadline = timezone.now() - timedelta(seconds=1)
        ExperimentSession.objects.filter(pk=session.pk).update(
            started_at=timezone.now() - timedelta(minutes=2), deadline_at=old_deadline,
            personal_deadline_at=old_deadline,
        )
        session.refresh_from_db()
        _terminate_for_deadline(session, now=old_deadline, reason="total_time_limit")
        append_schedule_revision(task, self.teacher, expected_revision=1, total_time_limit_minutes=3, reason="增加总时限")

        resumed_at = timezone.now()
        resumed = resume_task_session(task, student, request_received_at=resumed_at)
        self.assertEqual(resumed.total_time_limit_seconds, 180)
        self.assertGreater(resumed.deadline_at, resumed_at + timedelta(seconds=119))
        self.assertLess(resumed.deadline_at, resumed_at + timedelta(seconds=121))
        self.assertEqual(resume_task_session(task, student).deadline_at, resumed.deadline_at)

    def test_per_question_extension_restores_timed_out_attempt_without_replacing_close_event(self):
        self.experiment.time_mode = Experiment.TimeMode.PER_QUESTION
        self.experiment.per_question_time_limit_seconds = 30
        self.experiment.save()
        plan, _ = self.preview_two_classes()
        commit = self.client.post(self.plan_url(plan, "commit/"), data=json.dumps({"expected_revision": plan.plan_revision}), content_type="application/json")
        self.complete_operation(commit)
        task = ClassExperimentTask.objects.get(experiment=self.experiment, class_group=self.class_a)
        task.schedule_revisions.filter(revision_no=1).update(opens_at=timezone.now() - timedelta(hours=1))
        student = User.objects.get(student_number="PUB-001")
        session = get_or_create_session(self.experiment, student, class_task=task)
        attempt = record_visit(session.pk, session.assignments.get().pk)
        old_deadline = timezone.now() - timedelta(seconds=1)
        Attempt.objects.filter(pk=attempt.pk).update(item_deadline_at=old_deadline, initial_effective_deadline_at=old_deadline, active_seconds=30)
        attempt.refresh_from_db()
        _terminate_for_deadline(session, attempt=attempt, now=old_deadline, reason="item_time_limit")
        append_schedule_revision(task, self.teacher, expected_revision=1, per_question_time_limit_seconds=90, reason="增加单题时限")

        resumed_at = timezone.now()
        resumed = resume_task_session(task, student, request_received_at=resumed_at)
        attempt.refresh_from_db()
        self.assertEqual(resumed.recovery_round, 1)
        self.assertEqual(attempt.status, Attempt.Status.VISITED)
        self.assertEqual(attempt.active_seconds, 30)
        self.assertEqual(attempt.active_seconds_at_recovery, 30)
        self.assertEqual(attempt.initial_effective_deadline_at, old_deadline)
        self.assertGreater(attempt.current_effective_deadline_at, resumed_at + timedelta(seconds=59))
        self.assertEqual(AssessmentEvent.objects.filter(attempt=attempt, event_type="attempt_closed").count(), 1)
        self.assertEqual(AssessmentEvent.objects.filter(attempt=attempt, event_type="attempt_reopened").count(), 1)

    def test_active_question_gets_extended_deadline_but_retains_original_deadline_fact(self):
        self.experiment.time_mode = Experiment.TimeMode.PER_QUESTION
        self.experiment.per_question_time_limit_seconds = 30
        self.experiment.save()
        plan, _ = self.preview_two_classes()
        commit = self.client.post(self.plan_url(plan, "commit/"), data=json.dumps({"expected_revision": plan.plan_revision}), content_type="application/json")
        self.complete_operation(commit)
        task = ClassExperimentTask.objects.get(experiment=self.experiment, class_group=self.class_a)
        task.schedule_revisions.filter(revision_no=1).update(opens_at=timezone.now() - timedelta(hours=1))
        student = User.objects.get(student_number="PUB-001")
        session = get_or_create_session(self.experiment, student, class_task=task)
        attempt = record_visit(session.pk, session.assignments.get().pk)
        original = attempt.initial_effective_deadline_at
        append_schedule_revision(task, self.teacher, expected_revision=1, per_question_time_limit_seconds=90, reason="单题补时")

        resumed = resume_task_session(task, student)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, Attempt.Status.VISITED)
        self.assertEqual(attempt.initial_effective_deadline_at, original)
        self.assertEqual(attempt.current_effective_deadline_at, original + timedelta(seconds=60))
        self.assertTrue(DeadlineDecisionService.can_write_response(
            task, resumed, attempt, original + timedelta(seconds=1)
        ).allowed)

    def test_reopen_preserves_submitted_answer_and_only_restores_unsubmitted_item(self):
        question = Question.objects.create(
            bank=self.experiment.question_bank, sequence=2, code="PUB-Q2",
            question_type=Question.Type.SHORT, text="第二题", correct_answer="答案二",
        )
        QuestionConcept.objects.create(
            question=question, concept=Concept.objects.get(course=self.course), is_primary=True,
        )
        self.experiment.sampling_total_count = 2
        self.experiment.save()
        plan, _ = self.preview_two_classes()
        commit = self.client.post(self.plan_url(plan, "commit/"), data=json.dumps({"expected_revision": plan.plan_revision}), content_type="application/json")
        self.complete_operation(commit)
        task = ClassExperimentTask.objects.get(experiment=self.experiment, class_group=self.class_a)
        task.schedule_revisions.filter(revision_no=1).update(
            opens_at=timezone.now() - timedelta(hours=1), closes_at=timezone.now() + timedelta(minutes=1)
        )
        student = User.objects.get(student_number="PUB-001")
        session = get_or_create_session(self.experiment, student, class_task=task)
        assignments = list(session.assignments.order_by("assigned_sequence_index"))
        submitted = record_visit(session.pk, assignments[0].pk)
        save_draft(submitted.pk, "答案")
        submit_attempt(submitted.pk, uuid.uuid4())
        submitted.refresh_from_db()
        prior_answer = submitted.final_answer
        _terminate_for_deadline(session, now=timezone.now(), reason="deadline_expired")
        task.schedule_revisions.filter(revision_no=1).update(closes_at=timezone.now() - timedelta(seconds=1))
        append_schedule_revision(task, self.teacher, expected_revision=1, extend_minutes=5, reason="恢复未交题目")

        resume_task_session(task, student)
        submitted.refresh_from_db()
        pending = Attempt.objects.get(assignment=assignments[1])
        self.assertEqual(submitted.status, Attempt.Status.SUBMITTED)
        self.assertEqual(submitted.final_answer, prior_answer)
        self.assertEqual(AssessmentEvent.objects.filter(attempt=submitted, event_type="response_committed").count(), 1)
        self.assertFalse(AssessmentEvent.objects.filter(attempt=submitted, event_type="attempt_reopened").exists())
        self.assertEqual(pending.status, Attempt.Status.UNVISITED)

    def test_new_class_cutoff_caps_an_existing_unlimited_session_at_the_exact_instant(self):
        plan, _ = self.preview_two_classes()
        commit = self.client.post(self.plan_url(plan, "commit/"), data=json.dumps({"expected_revision": plan.plan_revision}), content_type="application/json")
        self.complete_operation(commit)
        task = ClassExperimentTask.objects.get(experiment=self.experiment, class_group=self.class_a)
        task.schedule_revisions.filter(revision_no=1).update(opens_at=timezone.now() - timedelta(hours=1))
        session = get_or_create_session(
            self.experiment, User.objects.get(student_number="PUB-001"), class_task=task
        )
        cutoff = timezone.now() + timedelta(minutes=5)
        append_schedule_revision(task, self.teacher, expected_revision=1, closes_at=cutoff, reason="设置本班截止")
        self.assertTrue(DeadlineDecisionService.can_enter_item(task, session, cutoff - timedelta(microseconds=1)).allowed)
        self.assertFalse(DeadlineDecisionService.can_enter_item(task, session, cutoff).allowed)

    def test_manual_close_pause_does_not_consume_personal_time_before_resume(self):
        self.experiment.time_mode = Experiment.TimeMode.TOTAL
        self.experiment.total_time_limit_minutes = 2
        self.experiment.save()
        plan, _ = self.preview_two_classes()
        commit = self.client.post(self.plan_url(plan, "commit/"), data=json.dumps({"expected_revision": plan.plan_revision}), content_type="application/json")
        self.complete_operation(commit)
        task = ClassExperimentTask.objects.get(experiment=self.experiment, class_group=self.class_a)
        task.schedule_revisions.filter(revision_no=1).update(opens_at=timezone.now() - timedelta(hours=1))
        student = User.objects.get(student_number="PUB-001")
        session = get_or_create_session(self.experiment, student, class_task=task)
        manual_close_task(task, self.teacher, reason="课堂暂停")
        revision = append_schedule_revision(
            task, self.teacher, expected_revision=1, reopen=True,
            closes_at=timezone.now() + timedelta(minutes=10), reason="课堂恢复",
        )
        resumed_at = revision.created_at + timedelta(minutes=1)

        resumed = resume_task_session(task, student, request_received_at=resumed_at)
        self.assertGreater(resumed.deadline_at, resumed_at + timedelta(seconds=119))
        self.assertLess(resumed.deadline_at, resumed_at + timedelta(seconds=121))
        call_command("expire_due_sessions", stdout=StringIO())
        resumed.refresh_from_db()
        self.assertEqual(resumed.status, ExperimentSession.Status.IN_PROGRESS)
