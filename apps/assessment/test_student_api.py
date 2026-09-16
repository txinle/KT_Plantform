import json
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase, override_settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.audit.models import AuditLog
from apps.experiments.models import Experiment, ExperimentSamplingKC
from apps.experiments.publication_services import (
    commit_publish_plan,
    generate_preview,
    get_or_create_publish_plan,
    save_plan_schedules,
    save_plan_selection,
)
from apps.question_bank.models import (
    Concept,
    Question,
    QuestionBank,
    QuestionConcept,
    QuestionOption,
)

from .models import (
    AssessmentEvent,
    Attempt,
    ConfidenceAssignment,
    ExperimentSession,
    GradeRecord,
    QuestionVisitEvent,
    SubmissionEvent,
)
from .test_support import accept_research_consent


class StudentApiFixtureMixin:
    feedback_mode = Experiment.Feedback.FULL

    def setUp(self):
        self.teacher = User.objects.create_user(
            username="student-api-teacher",
            password="test-password",
            role=User.Role.TEACHER,
        )
        self.student = User.objects.create_user(
            username="student-api-student",
            student_number="STUDENT-001",
            display_name="学生一",
            password="test-password",
            role=User.Role.STUDENT,
        )
        self.peer = User.objects.create_user(
            username="student-api-peer",
            student_number="STUDENT-002",
            display_name="学生二",
            password="test-password",
            role=User.Role.STUDENT,
        )
        self.outsider = User.objects.create_user(
            username="student-api-outsider",
            student_number="STUDENT-999",
            password="test-password",
            role=User.Role.STUDENT,
        )
        accept_research_consent(self.student, self.peer, self.outsider)
        self.course = Course.objects.create(
            name="学生 API 课程",
            code="STU-API",
            term="2026-Autumn",
            owner=self.teacher,
        )
        self.class_group = ClassGroup.objects.create(
            course=self.course,
            name="一班",
            academic_year="2026",
            owner=self.teacher,
        )
        for student in (self.student, self.peer):
            CourseEnrollment.objects.create(
                course=self.course,
                class_group=self.class_group,
                student=student,
            )
        self.bank = QuestionBank.objects.create(
            name="学生 API 题库",
            course=self.course,
            owner=self.teacher,
        )
        self.question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            code="STU-Q1",
            question_type=Question.Type.SINGLE,
            text="2 + 2 等于多少？",
            correct_answer="B",
            explanation="2 + 2 = 4。",
        )
        QuestionOption.objects.create(
            question=self.question,
            key="A",
            text="3",
            display_order=1,
        )
        QuestionOption.objects.create(
            question=self.question,
            key="B",
            text="4",
            display_order=2,
        )
        concept = Concept.objects.create(course=self.course, name="基础运算")
        QuestionConcept.objects.create(
            question=self.question,
            concept=concept,
            is_primary=True,
        )
        self.experiment, self.task = self.publish_task(
            name="正式学生任务",
            feedback_mode=self.feedback_mode,
        )

    def publish_task(
        self,
        *,
        name,
        feedback_mode,
        feedback_release_policy=None,
        opens_at=None,
        closes_at=None,
        research_tools=None,
        confidence_mode=None,
    ):
        values = {
            "name": name,
            "course": self.course,
            "question_bank": self.bank,
            "owner": self.teacher,
            "created_by": self.teacher,
            "feedback_mode": feedback_mode,
            "sampling_mode": Experiment.SamplingMode.TOTAL_COUNT,
            "sampling_total_count": self.bank.questions.filter(
                status=Question.Status.ACTIVE
            ).count(),
        }
        if feedback_release_policy is not None:
            values["feedback_release_policy"] = feedback_release_policy
        if research_tools is not None:
            values["research_tools"] = research_tools
        if confidence_mode is not None:
            values["confidence_mode"] = confidence_mode
        experiment = Experiment.objects.create(**values)
        ExperimentSamplingKC.objects.create(
            experiment=experiment,
            concept=self.question.primary_concept,
        )
        plan = get_or_create_publish_plan(experiment, self.teacher)
        plan = save_plan_selection(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
            class_ids=[self.class_group.pk],
            exclusions_by_class={},
        )
        plan = save_plan_schedules(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
            default_opens_at=opens_at,
            default_closes_at=closes_at,
            overrides={},
        )
        generate_preview(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
        )
        plan.refresh_from_db()
        task = commit_publish_plan(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
        )[0]
        return experiment, task

    def submit_attempt_without_finishing(self, session, *, answer="B", is_correct=True):
        attempt = session.attempts.get()
        now = timezone.now()
        attempt.status = Attempt.Status.SUBMITTED
        attempt.final_answer = answer
        attempt.submitted_at = now
        attempt.submission_sequence_index = attempt.assignment.assigned_sequence_index
        attempt.save(
            update_fields=[
                "status",
                "final_answer",
                "submitted_at",
                "submission_sequence_index",
            ]
        )
        submission = SubmissionEvent.objects.create(
            attempt=attempt,
            submission_token=uuid.uuid4(),
            final_answer=answer,
            server_received_at=now,
            committed_at=now,
            grading_status=SubmissionEvent.GradingStatus.COMPLETED,
        )
        GradeRecord.objects.create(
            submission=submission,
            version=1,
            status=GradeRecord.Status.COMPLETED,
            validity_status=GradeRecord.ValidityStatus.EFFECTIVE,
            is_correct=is_correct,
            score=Decimal("1") if is_correct else Decimal("0"),
            max_score=Decimal("1"),
            graded_at=now,
        )
        return attempt

    def start(self, *, task=None, student=None):
        active_student = student or self.student
        accept_research_consent(active_student)
        self.client.force_login(active_student)
        return self.client.post(
            f"/api/v1/student/tasks/{(task or self.task).pk}/start/",
            data={},
            content_type="application/json",
        )

    def complete_session(self, session, *, answer="B", is_correct=True):
        now = timezone.now()
        for attempt in session.attempts.select_related("assignment"):
            attempt.status = Attempt.Status.SUBMITTED
            attempt.final_answer = answer
            attempt.submitted_at = now
            attempt.submission_sequence_index = attempt.assignment.assigned_sequence_index
            attempt.save(
                update_fields=[
                    "status",
                    "final_answer",
                    "submitted_at",
                    "submission_sequence_index",
                ]
            )
            submission = SubmissionEvent.objects.create(
                attempt=attempt,
                submission_token=uuid.uuid4(),
                final_answer=answer,
                server_received_at=now,
                committed_at=now,
                grading_status=SubmissionEvent.GradingStatus.COMPLETED,
            )
            GradeRecord.objects.create(
                submission=submission,
                version=1,
                status=GradeRecord.Status.COMPLETED,
                validity_status=GradeRecord.ValidityStatus.EFFECTIVE,
                is_correct=is_correct,
                score=Decimal("1") if is_correct else Decimal("0"),
                max_score=Decimal("1"),
                graded_at=now,
            )
        session.status = ExperimentSession.Status.COMPLETED
        session.completed_at = now
        session.terminal_at = now
        session.save(update_fields=["status", "completed_at", "terminal_at"])
        return session


class StudentApiTestCase(StudentApiFixtureMixin, TestCase):
    pass


class StudentTaskApiTests(StudentApiTestCase):
    @override_settings(CAPTURE_V2_ENABLED=True, CAPTURE_V2_COURSE_IDS=frozenset({'*'}), CAPTURE_V2_USER_IDS=frozenset())
    def test_all_courses_rollout_pins_new_sessions_to_v2(self):
        started = self.start()
        self.assertEqual(started.status_code, 201, started.content)
        session = ExperimentSession.objects.get(pk=started.json()['session_id'])
        self.assertEqual(session.capture_contract.contract_version, 2)

    def test_enabling_all_courses_does_not_relabel_existing_v1_sessions(self):
        with override_settings(CAPTURE_V2_ENABLED=False):
            started = self.start()
        self.assertEqual(started.status_code, 201, started.content)
        session = ExperimentSession.objects.get(pk=started.json()['session_id'])
        original_contract = session.capture_contract_id
        with override_settings(CAPTURE_V2_ENABLED=True, CAPTURE_V2_COURSE_IDS=frozenset({'*'})):
            resumed = self.start()
        self.assertEqual(resumed.status_code, 200, resumed.content)
        self.assertEqual(resumed.json()['session_id'], session.pk)
        session.refresh_from_db()
        self.assertEqual(session.capture_contract_id, original_contract)
        self.assertEqual(session.capture_contract.contract_version, 1)

    def test_task_list_query_count_does_not_scale_with_task_count(self):
        self.client.force_login(self.student)
        url = f"/api/v1/student/courses/{self.course.pk}/tasks/"
        with CaptureQueriesContext(connection) as one_task_queries:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, response.content)

        self.publish_task(name="第二个正式学生任务", feedback_mode=self.feedback_mode)
        self.publish_task(name="第三个正式学生任务", feedback_mode=self.feedback_mode)
        with CaptureQueriesContext(connection) as three_task_queries:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, response.content)

        self.assertEqual(len(three_task_queries), len(one_task_queries))

    def test_course_collection_and_task_list_are_student_scoped_allow_lists(self):
        self.client.force_login(self.student)

        courses = self.client.get("/api/v1/student/courses/")
        tasks = self.client.get(
            f"/api/v1/student/courses/{self.course.pk}/tasks/"
        )

        self.assertEqual(courses.status_code, 200, courses.content)
        self.assertEqual(
            courses.json(),
            {
                "items": [
                    {
                        "course_id": self.course.pk,
                        "course_name": "学生 API 课程",
                        "course_code": "STU-API",
                        "course_term": "2026-Autumn",
                        "course_status": Course.Status.ACTIVE,
                        "class_name": "一班",
                    }
                ]
            },
        )
        self.assertEqual(tasks.status_code, 200, tasks.content)
        self.assertEqual(set(tasks.json()), {"course", "items"})
        item = tasks.json()["items"][0]
        self.assertEqual(
            set(item),
            {
                "task_id",
                "experiment_id",
                "experiment_name",
                "class_name",
                "business_status",
                "opens_at",
                "closes_at",
                "progress",
                "primary_action",
                "available_actions",
                "feedback_mode",
            },
        )
        self.assertEqual(item["task_id"], self.task.pk)
        self.assertEqual(item["business_status"], "open")
        self.assertEqual(item["progress"], {"completed": 0, "total": 1})
        self.assertEqual(item["primary_action"], "start")
        self.assertEqual(item["available_actions"], ["start"])
        self.assertEqual(item["feedback_mode"], Experiment.Feedback.FULL)

        self.client.force_login(self.outsider)
        hidden = self.client.get(
            f"/api/v1/student/courses/{self.course.pk}/tasks/"
        )
        self.assertEqual(hidden.status_code, 404)

    def test_task_start_and_resume_bind_the_authoritative_task_and_participant(self):
        started = self.start()

        self.assertEqual(started.status_code, 201, started.content)
        session = ExperimentSession.objects.get(student=self.student)
        participant = self.task.participant_snapshots.get(student=self.student)
        self.assertEqual(session.class_task, self.task)
        self.assertEqual(session.participant_snapshot, participant)
        self.assertEqual(started.json()["session_id"], session.pk)
        self.assertEqual(
            started.json()["question_url"],
            reverse(
                "assessment:question",
                args=[session.pk, session.assignments.get().pk],
            ),
        )

        repeated = self.start()
        self.assertEqual(repeated.status_code, 200, repeated.content)
        self.assertEqual(repeated.json()["session_id"], session.pk)
        resumed = self.client.post(
            f"/api/v1/student/tasks/{self.task.pk}/resume/",
            data={},
            content_type="application/json",
        )
        self.assertEqual(resumed.status_code, 200, resumed.content)
        self.assertEqual(resumed.json()["session_id"], session.pk)
        self.assertEqual(ExperimentSession.objects.filter(student=self.student).count(), 1)

    def test_resume_requires_an_existing_own_session(self):
        self.client.force_login(self.student)

        missing = self.client.post(
            f"/api/v1/student/tasks/{self.task.pk}/resume/",
            data={},
            content_type="application/json",
        )

        self.assertEqual(missing.status_code, 409)
        self.assertEqual(missing.json()["error"]["code"], "SESSION_NOT_STARTED")

        self.client.force_login(self.outsider)
        hidden = self.client.post(
            f"/api/v1/student/tasks/{self.task.pk}/start/",
            data={},
            content_type="application/json",
        )
        self.assertEqual(hidden.status_code, 404)

    def test_legacy_start_is_post_only_and_resolves_the_unique_formal_task(self):
        self.client.force_login(self.student)
        url = reverse("assessment:start", args=[self.experiment.pk])

        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url)

        self.assertEqual(response.status_code, 302)
        session = ExperimentSession.objects.get(student=self.student)
        self.assertEqual(session.class_task, self.task)
        self.assertEqual(
            response.url,
            reverse(
                "assessment:question",
                args=[session.pk, session.assignments.get().pk],
            ),
        )




class StudentAssessmentApiTests(StudentApiTestCase):
    @override_settings(CAPTURE_V2_ENABLED=True, CAPTURE_V2_COURSE_IDS=frozenset({"*"}), CAPTURE_V2_USER_IDS=frozenset())
    def test_capture_v2_question_entry_omits_research_url_without_tools_or_rules(self):
        cases = (
            ("none", {}, Experiment.ConfidenceMode.OFF),
            ("kc-only", {"kc_visible": True}, Experiment.ConfidenceMode.OFF),
            ("confidence-only", {}, Experiment.ConfidenceMode.ALL),
            ("kc-and-confidence", {"kc_visible": True}, Experiment.ConfidenceMode.ALL),
        )
        for name, tools, confidence_mode in cases:
            with self.subTest(name=name):
                _, task = self.publish_task(
                    name=f"无研究工具-{name}", feedback_mode=self.feedback_mode,
                    research_tools=tools, confidence_mode=confidence_mode,
                )
                started = self.start(task=task)
                self.assertEqual(started.status_code, 201, started.content)
                session = ExperimentSession.objects.get(pk=started.json()["session_id"])
                assignment = session.assignments.get()
                response = self.client.get(
                    f"/api/v1/student/sessions/{session.pk}/questions/{assignment.pk}/"
                )
                self.assertEqual(response.status_code, 200, response.content)
                self.assertNotIn("research_tools_url", response.json()["actions"])

    @override_settings(CAPTURE_V2_ENABLED=True, CAPTURE_V2_COURSE_IDS=frozenset({"*"}), CAPTURE_V2_USER_IDS=frozenset())
    def test_capture_v2_question_entry_keeps_research_url_for_tools_or_rules(self):
        hint = {
            "id": "hint-1", "question_code": "STU-Q1", "level": 1,
            "text": "想一想加法", "contains_answer": False,
        }
        rule = {
            "id": "rule-1", "tool": "confidence", "trigger": "elapsed_seconds",
            "question_code": "STU-Q1", "after_seconds": 10,
            "max_exposures": 1, "required": False, "hint_id": "",
        }
        cases = (
            ("doubt", {"doubt_enabled": True}),
            ("check", {"check_enabled": True}),
            ("hint", {"hints": [hint]}),
            ("rule", {"rules": [rule]}),
        )
        for name, tools in cases:
            with self.subTest(name=name):
                _, task = self.publish_task(
                    name=f"有研究工具-{name}", feedback_mode=self.feedback_mode,
                    research_tools=tools,
                )
                started = self.start(task=task)
                self.assertEqual(started.status_code, 201, started.content)
                session = ExperimentSession.objects.get(pk=started.json()["session_id"])
                assignment = session.assignments.get()
                response = self.client.get(
                    f"/api/v1/student/sessions/{session.pk}/questions/{assignment.pk}/"
                )
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(
                    response.json()["actions"]["research_tools_url"],
                    reverse("assessment_process:research_tools", args=[assignment.attempt.pk]),
                )

    def test_navigation_entry_returns_visit_contract_and_replays_navigation_id(self):
        self.start()
        session = ExperimentSession.objects.get(
            class_task=self.task,
            student=self.student,
        )
        assignment = session.assignments.get()
        navigation_id = uuid.uuid4()
        clicked_at = timezone.now() - timedelta(seconds=1)
        time_origin = clicked_at - timedelta(seconds=20)
        url = f"/api/v1/student/sessions/{session.pk}/questions/{assignment.pk}/"
        query = {
            "reason": "navigation_click",
            "navigation_id": str(navigation_id),
            "client_occurred_at": clicked_at.isoformat(),
            "client_monotonic_ms": "20000.5",
            "client_time_origin": time_origin.isoformat(),
        }

        first = self.client.get(url, query)
        replay = self.client.get(url, query)

        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(replay.status_code, 200, replay.content)
        process = first.json()["process"]
        self.assertEqual(process["navigation_id"], str(navigation_id))
        self.assertEqual(process["visit_index"], 1)
        self.assertEqual(replay.json()["process"]["visit_id"], process["visit_id"])
        self.assertEqual(
            replay.json()["process"]["entry_event_id"],
            process["entry_event_id"],
        )
        self.assertEqual(QuestionVisitEvent.objects.filter(session=session).count(), 1)
        entered = AssessmentEvent.objects.get(pk=process["entry_event_id"])
        self.assertEqual(entered.correlation_id, navigation_id)
        self.assertEqual(entered.payload["visit_id"], process["visit_id"])

    @override_settings(
        CAPTURE_V2_ENABLED=True,
        CAPTURE_V2_COURSE_IDS=frozenset({"*"}),
        CAPTURE_V2_USER_IDS=frozenset(),
    )
    def test_capture_v2_question_entry_exposes_the_writer_release_url(self):
        self.start()
        session = ExperimentSession.objects.get(
            class_task=self.task,
            student=self.student,
        )
        assignment = session.assignments.get()

        response = self.client.get(
            f"/api/v1/student/sessions/{session.pk}/questions/{assignment.pk}/"
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            response.json()["process"]["writer_lease_release_url"],
            reverse(
                "assessment_process:writer_lease_release",
                args=[assignment.attempt.pk],
            ),
        )

    @override_settings(
        CAPTURE_V2_ENABLED=False,
        CAPTURE_V2_USER_IDS=frozenset(),
        CAPTURE_V2_COURSE_IDS=frozenset(),
    )
    def test_question_entry_returns_only_authoritative_student_runtime_state(self):
        self.start()
        session = ExperimentSession.objects.get(
            class_task=self.task,
            student=self.student,
        )
        self.assertEqual(session.capture_contract.contract_name, "capture-v1")
        assignment = session.assignments.select_related("snapshot").get()

        response = self.client.get(
            f"/api/v1/student/sessions/{session.pk}/questions/{assignment.pk}/"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            set(payload),
            {
                "task_id",
                "session_id",
                "session_status",
                "experiment_name",
                "assignment",
                "progress",
                "remaining_seconds",
                "question",
                "draft_answer",
                "initial_response_state",
                "pending_confidence",
                "navigation",
                "actions",
                "process",
            },
        )
        self.assertEqual(payload["task_id"], self.task.pk)
        self.assertEqual(payload["session_id"], session.pk)
        self.assertEqual(payload["session_status"], "in_progress")
        self.assertEqual(payload["experiment_name"], self.experiment.name)
        self.assertEqual(
            payload["assignment"],
            {
                "assignment_id": assignment.pk,
                "attempt_id": assignment.attempt.pk,
                "status": "visited",
                "sequence_index": 1,
            },
        )
        self.assertEqual(payload["progress"], {"submitted": 0, "total": 1})
        self.assertIsNone(payload["remaining_seconds"])
        self.assertEqual(
            payload["question"],
            {
                "snapshot_id": assignment.snapshot_id,
                "stable_code": assignment.snapshot.stable_code,
                "code": "STU-Q1",
                "question_type": "single_choice",
                "text": "2 + 2 等于多少？",
                "primary_kc": "基础运算",
                "options": [
                    {"key": "A", "text": "3"},
                    {"key": "B", "text": "4"},
                ],
            },
        )
        self.assertIsNone(payload["draft_answer"])
        self.assertIsNone(payload["initial_response_state"])
        self.assertIsNone(payload["pending_confidence"])
        self.assertEqual(
            payload["navigation"],
            [
                {
                    "assignment_id": assignment.pk,
                    "sequence_index": 1,
                    "status": "visited",
                    "url": reverse(
                        "assessment:question",
                        args=[session.pk, assignment.pk],
                    ),
                    "current": True,
                }
            ],
        )
        self.assertEqual(
            payload["actions"],
            {
                "save_draft_url": reverse(
                    "assessment:save_draft", args=[assignment.attempt.pk]
                ),
                "submit_url": reverse(
                    "assessment:submit", args=[assignment.attempt.pk]
                ),
                "skip_url": reverse(
                    "assessment:skip", args=[assignment.attempt.pk]
                ),
                "finish_url": reverse("assessment:finish", args=[session.pk]),
            },
        )
        self.assertEqual(payload["process"]["session_id"], session.pk)
        self.assertEqual(payload["process"]["attempt_id"], assignment.attempt.pk)
        self.assertEqual(payload["process"]["app_version"], "2026.08.29.2")
        self.assertEqual(payload["process"]["heartbeat_interval_seconds"], 30)
        serialized = response.content.decode("utf-8")
        for forbidden in ("correct_answer", "explanation", "is_correct", "score"):
            self.assertNotIn(forbidden, serialized)

        assignment.attempt.refresh_from_db()
        self.assertEqual(assignment.attempt.visit_count, 1)
        self.assertIsNotNone(assignment.attempt.first_opened_at)
        self.assertIsNotNone(assignment.attempt.last_opened_at)

    def test_question_entry_hides_another_students_session(self):
        self.start(student=self.peer)
        peer_session = ExperimentSession.objects.get(
            class_task=self.task,
            student=self.peer,
        )
        peer_assignment = peer_session.assignments.get()
        self.client.force_login(self.student)

        response = self.client.get(
            f"/api/v1/student/sessions/{peer_session.pk}/questions/{peer_assignment.pk}/"
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "NOT_FOUND")

    def test_unresolved_confidence_does_not_redirect_another_question(self):
        second_question = Question.objects.create(
            bank=self.bank,
            sequence=2,
            code="STU-Q2",
            question_type=Question.Type.SINGLE,
            text="3 + 3 等于多少？",
            correct_answer="B",
        )
        QuestionOption.objects.create(
            question=second_question,
            key="A",
            text="5",
            display_order=1,
        )
        QuestionOption.objects.create(
            question=second_question,
            key="B",
            text="6",
            display_order=2,
        )
        QuestionConcept.objects.create(
            question=second_question,
            concept=Concept.objects.get(course=self.course, name="基础运算"),
            is_primary=True,
        )
        _experiment, task = self.publish_task(
            name="信心评价导航任务",
            feedback_mode=self.feedback_mode,
        )
        started = self.start(task=task)
        session = ExperimentSession.objects.get(pk=started.json()["session_id"])
        first, second = session.assignments.order_by("assigned_sequence_index")
        first_attempt = first.attempt
        first_attempt.status = Attempt.Status.SUBMITTED
        first_attempt.submitted_at = timezone.now()
        first_attempt.save(update_fields=["status", "submitted_at"])
        confidence = ConfidenceAssignment.objects.get(attempt=first_attempt)
        confidence.condition = ConfidenceAssignment.Condition.PROMPT
        confidence.response_status = ConfidenceAssignment.ResponseStatus.ASSIGNED
        confidence.save(update_fields=["condition", "response_status"])

        response = self.client.get(
            f"/api/v1/student/sessions/{session.pk}/questions/{second.pk}/"
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['assignment']['assignment_id'], second.pk)

    def test_full_feedback_never_reveals_answers_during_an_active_session(self):
        started = self.start()
        session = ExperimentSession.objects.get(pk=started.json()["session_id"])
        attempt = session.attempts.get()
        attempt.status = Attempt.Status.SUBMITTED
        attempt.final_answer = "B"
        attempt.submitted_at = timezone.now()
        attempt.save(update_fields=["status", "final_answer", "submitted_at"])
        SubmissionEvent.objects.create(
            attempt=attempt,
            submission_token=uuid.uuid4(),
            final_answer="B",
            server_received_at=timezone.now(),
            committed_at=timezone.now(),
            grading_status=SubmissionEvent.GradingStatus.PENDING,
        )

        response = self.client.get(
            f"/api/v1/student/tasks/{self.task.pk}/results/"
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn("items", response.json())

    def test_new_policy_waits_for_whole_session_completion(self):
        self.assertEqual(
            self.experiment.feedback_release_policy,
            "after_personal_submission",
        )
        started = self.start()
        session = ExperimentSession.objects.get(pk=started.json()["session_id"])
        self.submit_attempt_without_finishing(session)

        before_completion = self.client.get(
            f"/api/v1/student/tasks/{self.task.pk}/results/"
        )
        self.assertEqual(before_completion.status_code, 200, before_completion.content)
        self.assertNotIn("items", before_completion.json())

        now = timezone.now()
        session.status = ExperimentSession.Status.COMPLETED
        session.completed_at = now
        session.terminal_at = now
        session.save(update_fields=["status", "completed_at", "terminal_at"])
        released = self.client.get(f"/api/v1/student/tasks/{self.task.pk}/results/")

        self.assertEqual(released.status_code, 200, released.content)
        self.assertEqual(released.json()["items"][0]["correct_answer"], "B：4")

    def test_pending_grade_never_projects_correctness_or_full_feedback(self):
        started = self.start()
        session = ExperimentSession.objects.get(pk=started.json()["session_id"])
        attempt = session.attempts.get()
        now = timezone.now()
        attempt.status = Attempt.Status.SUBMITTED
        attempt.final_answer = "B"
        attempt.submitted_at = now
        attempt.save(update_fields=["status", "final_answer", "submitted_at"])
        submission = SubmissionEvent.objects.create(
            attempt=attempt,
            submission_token=uuid.uuid4(),
            final_answer="B",
            server_received_at=now,
            committed_at=now,
            grading_status=SubmissionEvent.GradingStatus.PENDING,
        )
        GradeRecord.objects.create(
            submission=submission,
            version=1,
            status=GradeRecord.Status.PENDING,
            validity_status=GradeRecord.ValidityStatus.CANDIDATE,
            grading_method="manual_short_answer_v1",
        )
        session.status = ExperimentSession.Status.COMPLETED
        session.completed_at = now
        session.terminal_at = now
        session.save(update_fields=["status", "completed_at", "terminal_at"])

        from .confidence_services import frozen_feedback_payload

        payload = frozen_feedback_payload(attempt)
        result = self.client.get(f"/api/v1/student/tasks/{self.task.pk}/results/")

        self.assertEqual(
            payload,
            {"feedback_mode": "full", "feedback_phase": "pending_grading"},
        )
        self.assertEqual(result.status_code, 200, result.content)
        item = result.json()["items"][0]
        for forbidden in (
            "is_correct",
            "score",
            "max_score",
            "correct_answer",
            "explanation",
        ):
            self.assertNotIn(forbidden, item)

    @override_settings(STUDENT_SESSION_IDLE_TIMEOUT_SECONDS=3 * 60 * 60)
    def test_deadline_policy_releases_results_only_after_the_class_deadline(self):
        now = timezone.now()
        _experiment, task = self.publish_task(
            name="截止后反馈任务",
            feedback_mode=Experiment.Feedback.FULL,
            feedback_release_policy="after_assignment_deadline",
            opens_at=now - timedelta(hours=1),
            closes_at=now + timedelta(hours=1),
        )
        started = self.start(task=task)
        session = ExperimentSession.objects.get(pk=started.json()["session_id"])
        self.submit_attempt_without_finishing(session)

        before_deadline = self.client.get(
            f"/api/v1/student/tasks/{task.pk}/results/"
        )
        self.assertEqual(before_deadline.status_code, 200, before_deadline.content)
        self.assertNotIn("items", before_deadline.json())

        with patch(
            "apps.assessment.feedback_services.timezone.now",
            return_value=now + timedelta(hours=2),
        ):
            released = self.client.get(f"/api/v1/student/tasks/{task.pk}/results/")

        self.assertEqual(released.status_code, 200, released.content)
        self.assertEqual(released.json()["items"][0]["correct_answer"], "B：4")

    def test_manual_release_is_audited_before_results_are_exposed(self):
        _experiment, task = self.publish_task(
            name="人工发布反馈任务",
            feedback_mode=Experiment.Feedback.FULL,
            feedback_release_policy="manual_release",
        )
        started = self.start(task=task)
        session = ExperimentSession.objects.get(pk=started.json()["session_id"])
        self.complete_session(session)

        blocked = self.client.get(f"/api/v1/student/tasks/{task.pk}/results/")
        self.assertEqual(blocked.status_code, 200, blocked.content)
        self.assertNotIn("items", blocked.json())

        self.client.force_login(self.teacher)
        release = self.client.post(
            f"/api/v1/teacher/class-tasks/{task.pk}/feedback-release/",
            data=json.dumps({"reason": "评分复核结束"}),
            content_type="application/json",
        )
        self.assertEqual(release.status_code, 200, release.content)
        released_task = type(task).objects.get(pk=task.pk)
        self.client.force_login(self.student)
        released = self.client.get(f"/api/v1/student/tasks/{task.pk}/results/")

        self.assertIsNotNone(released_task.feedback_released_at)
        self.assertEqual(released_task.feedback_released_by_id, self.teacher.pk)
        self.assertEqual(released.status_code, 200, released.content)
        self.assertEqual(released.json()["items"][0]["correct_answer"], "B：4")
        self.assertTrue(
            AuditLog.objects.filter(
                action="class_task.feedback_release",
                object_id=str(task.pk),
                actor=self.teacher,
            ).exists()
        )

    def test_missing_frozen_policy_is_legacy_not_the_new_default(self):
        from .feedback_services import feedback_release_policy_for_configuration

        self.assertEqual(
            feedback_release_policy_for_configuration({}),
            "legacy",
        )

    def test_none_feedback_returns_only_completion_metadata(self):
        none_experiment, none_task = self.publish_task(
            name="无反馈任务",
            feedback_mode=Experiment.Feedback.NONE,
        )
        del none_experiment
        response = self.start(task=none_task)
        session = ExperimentSession.objects.get(pk=response.json()["session_id"])
        self.complete_session(session)

        result = self.client.get(
            f"/api/v1/student/tasks/{none_task.pk}/results/"
        )

        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(
            set(result.json()),
            {
                "feedback_mode",
                "task_id",
                "completion_status",
                "submission_status",
                "completed_at",
                "grading_status",
            },
        )
        self.assertEqual(result.json()["feedback_mode"], Experiment.Feedback.NONE)
        serialized = str(result.json())
        for forbidden in (
            "score",
            "answer",
            "correct",
            "explanation",
            "average",
            "rank",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_correctness_and_full_feedback_are_server_trimmed(self):
        correctness_experiment, correctness_task = self.publish_task(
            name="正误反馈任务",
            feedback_mode=Experiment.Feedback.CORRECTNESS,
        )
        del correctness_experiment
        correctness_start = self.start(task=correctness_task)
        correctness_session = ExperimentSession.objects.get(
            pk=correctness_start.json()["session_id"]
        )
        self.complete_session(correctness_session)
        correctness = self.client.get(
            f"/api/v1/student/tasks/{correctness_task.pk}/results/"
        )

        full_start = self.start(task=self.task)
        full_session = ExperimentSession.objects.get(pk=full_start.json()["session_id"])
        self.complete_session(full_session)
        full = self.client.get(
            f"/api/v1/student/tasks/{self.task.pk}/results/"
        )

        self.assertEqual(correctness.status_code, 200, correctness.content)
        correctness_payload = correctness.json()
        self.assertEqual(
            correctness_payload["items"][0]["submitted_answer"], "B：4"
        )
        self.assertTrue(correctness_payload["items"][0]["is_correct"])
        self.assertNotIn("correct_answer", correctness_payload["items"][0])
        self.assertNotIn("explanation", correctness_payload["items"][0])

        self.assertEqual(full.status_code, 200, full.content)
        full_item = full.json()["items"][0]
        self.assertEqual(full_item["correct_answer"], "B：4")
        self.assertEqual(full_item["explanation"], "2 + 2 = 4。")

    def test_result_fields_are_removed_when_the_class_policy_forbids_them(self):
        policy = self.class_group.student_analytics_policy
        policy.show_scores = False
        policy.show_correct_rate = False
        policy.show_item_results = False
        policy.show_class_average = False
        policy.rank_mode = policy.RankMode.NONE
        policy.save(
            update_fields=[
                "show_scores",
                "show_correct_rate",
                "show_item_results",
                "show_class_average",
                "rank_mode",
            ]
        )
        started = self.start()
        self.complete_session(
            ExperimentSession.objects.get(pk=started.json()["session_id"])
        )

        response = self.client.get(
            f"/api/v1/student/tasks/{self.task.pk}/results/"
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            set(response.json()),
            {
                "feedback_mode",
                "task_id",
                "completion_status",
                "submission_status",
                "completed_at",
                "grading_status",
            },
        )

    def test_small_comparison_cohort_is_explicitly_unavailable_without_numbers(self):
        for student in (self.student, self.peer):
            response = self.start(student=student)
            session = ExperimentSession.objects.get(pk=response.json()["session_id"])
            self.complete_session(session)

        self.client.force_login(self.student)
        result = self.client.get(
            f"/api/v1/student/tasks/{self.task.pk}/results/"
        )

        self.assertEqual(result.status_code, 200, result.content)
        comparison = result.json()["comparison"]
        self.assertEqual(
            comparison,
            {
                "class_average": {
                    "status": "unavailable",
                    "reason": "minimum_sample",
                },
                "rank": {
                    "status": "unavailable",
                    "reason": "minimum_sample",
                },
            },
        )

    def test_comparison_is_available_only_after_five_fully_graded_students(self):
        cohort = [self.student, self.peer]
        for index in range(3):
            student = User.objects.create_user(
                username=f"student-api-cohort-{index}",
                student_number=f"STUDENT-C-{index}",
                password="test-password",
                role=User.Role.STUDENT,
            )
            CourseEnrollment.objects.create(
                course=self.course,
                class_group=self.class_group,
                student=student,
            )
            cohort.append(student)
        _experiment, task = self.publish_task(
            name="五人比较任务",
            feedback_mode=Experiment.Feedback.FULL,
        )
        for student in cohort:
            response = self.start(task=task, student=student)
            self.complete_session(
                ExperimentSession.objects.get(pk=response.json()["session_id"])
            )

        self.client.force_login(self.student)
        with CaptureQueriesContext(connection) as result_queries:
            result = self.client.get(f"/api/v1/student/tasks/{task.pk}/results/")

        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(
            result.json()["comparison"],
            {
                "class_average": {
                    "status": "available",
                    "percent": "100.0",
                    "sample_size": 5,
                },
                "rank": {
                    "status": "available",
                    "position": 1,
                    "sample_size": 5,
                },
            },
        )
        # Sliding session renewal and the current research-consent check are
        # fixed per request and do not scale with the task or cohort count.
        self.assertLessEqual(len(result_queries), 21)

    def test_result_openapi_contract_is_a_feedback_mode_discriminated_union(self):
        self.client.force_login(self.student)

        response = self.client.get(
            "/api/v1/schema/", HTTP_ACCEPT="application/json"
        )

        self.assertEqual(response.status_code, 200, response.content)
        schema = response.json()["paths"][
            "/api/v1/student/tasks/{task_id}/results/"
        ]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        if "$ref" in schema:
            schema = response.json()["components"]["schemas"][
                schema["$ref"].rsplit("/", 1)[-1]
            ]
        self.assertIn("oneOf", schema)
        self.assertEqual(schema["discriminator"]["propertyName"], "feedback_mode")


@override_settings(
    CLASS_CENTER_ENABLED=True,
    STUDENT_LEARNING_ANALYTICS_ENABLED=True,
)
class StudentLearningApiTests(StudentApiFixtureMixin, TransactionTestCase):
    def test_learning_summary_detail_and_question_are_own_student_allow_lists(self):
        response = self.start()
        session = ExperimentSession.objects.get(pk=response.json()["session_id"])
        self.complete_session(session)
        question_snapshot = session.assignments.get().snapshot

        summary = self.client.get(
            f"/api/v1/student/courses/{self.course.pk}/learning/"
        )
        detail = self.client.get(
            f"/api/v1/student/courses/{self.course.pk}/learning/"
            f"experiments/{self.experiment.pk}/"
        )
        question = self.client.get(
            f"/api/v1/student/courses/{self.course.pk}/learning/"
            f"experiments/{self.experiment.pk}/questions/{question_snapshot.pk}/"
        )

        self.assertEqual(summary.status_code, 200, summary.content)
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertEqual(question.status_code, 200, question.content)
        self.assertEqual(summary.json()["course_id"], self.course.pk)
        self.assertNotIn("experiment_rank_available", summary.json())
        for row in summary.json()["experiments"]:
            self.assertNotIn("rank", row)
        self.assertEqual(detail.json()["experiment_id"], self.experiment.pk)
        question_payload = question.json()
        self.assertEqual(question_payload["correct_answer"], "B：4")
        self.assertEqual(question_payload["score"]["value"], "1.00")
        self.assertEqual(question_payload["score"]["maximum"], "1.00")
        self.assertEqual(detail.json()["item_results"][0]["score"]["value"], "1.00")
        self.assertEqual(detail.json()["item_results"][0]["score"]["maximum"], "1.00")
        self.assertEqual(question_payload["question_type"], Question.Type.SINGLE)
        self.assertEqual(question_payload["result_state"], "correct")
        self.assertEqual(
            question_payload["options"],
            [
                {
                    "value": "A",
                    "label": "A",
                    "text": "3",
                    "selected_by_student": False,
                    "is_correct": False,
                },
                {
                    "value": "B",
                    "label": "B",
                    "text": "4",
                    "selected_by_student": True,
                    "is_correct": True,
                },
            ],
        )
        serialized = str([summary.json(), detail.json(), question.json()])
        self.assertNotIn(self.peer.student_number, serialized)
        self.assertNotIn(self.peer.shown_name, serialized)

    @override_settings(CAPTURE_V2_ENABLED=True)
    def test_capture_v2_detail_records_only_authorized_feedback_exposure_stages(self):
        from .capture_v2_services import ingest_capture_batch
        from .models import AssessmentEvent
        from .response_snapshot_services import ResponseState
        from .services import save_draft, submit_attempt

        with override_settings(CAPTURE_V2_USER_IDS=frozenset({str(self.student.pk)})):
            started = self.start()
        session = ExperimentSession.objects.get(pk=started.json()["session_id"])
        attempt = session.attempts.select_related("assignment__snapshot").get()
        stream = uuid.uuid4()
        transition_id = uuid.uuid4()
        before = ResponseState(Question.Type.SINGLE, None)
        after = ResponseState(Question.Type.SINGLE, "B")
        now = timezone.now()
        transition = {
            "event_id": str(transition_id),
            "event_type": "response_transition",
            "event_version": 1,
            "session_id": session.pk,
            "attempt_id": attempt.pk,
            "client_sequence_no": 1,
            "client_wall_time_utc": now.isoformat(),
            "client_monotonic_ms": 100,
            "client_time_origin": "2026-09-06T00:00:00Z",
            "quality_flags": [],
            "app_version": "capture-v2-test",
            "payload": {
                "capture_version": "capture-v2",
                "operation_id": str(uuid.uuid4()),
                "branch_id": str(stream),
                "parent_event_id": None,
                "before_state": before.state_json,
                "after_state": after.state_json,
                "before_state_hash": before.state_hash,
                "after_state_hash": after.state_hash,
                "input_type": "insertText",
                "source": "student",
            },
        }
        ingest_capture_batch(
            student=self.student,
            document={"client_instance_id": str(stream), "events": [transition]},
        )
        save_draft(
            attempt.pk,
            "ignored-by-capture-v2",
            target_event_id=str(transition_id),
            expected_version=0,
        )
        final_attempt = submit_attempt(
            attempt.pk,
            uuid.uuid4(),
            target_event_id=str(transition_id),
        )
        session.refresh_from_db()
        session.status = ExperimentSession.Status.COMPLETED
        session.completed_at = now
        session.terminal_at = now
        session.save(update_fields=["status", "completed_at", "terminal_at"])

        question = self.client.get(
            f"/api/v1/student/courses/{self.course.pk}/learning/"
            f"experiments/{self.experiment.pk}/questions/"
            f"{attempt.assignment.snapshot_id}/"
        )

        self.assertEqual(question.status_code, 200, question.content)
        capture = question.json()["feedback_capture"]
        self.assertEqual(capture["capture_version"], "capture-v2")
        self.assertEqual(capture["answer_event_id"], str(final_attempt.final_response_event_id))
        self.assertEqual(capture["snapshot_id"], str(final_attempt.final_response_snapshot_id))
        self.assertEqual(capture["mode"], Experiment.Feedback.FULL)
        self.assertEqual(
            capture["authorized"],
            {"correctness": True, "correct_answer": True, "explanation": True},
        )

        visit_id = str(uuid.uuid4())
        stages = (
            ("detail_entered", (True, False, False)),
            ("answer_presented", (True, True, False)),
            ("explanation_presented", (True, True, True)),
            ("detail_left", (True, True, True)),
        )
        events = []
        for sequence, (stage, flags) in enumerate(stages, start=2):
            events.append(
                {
                    "event_id": str(uuid.uuid4()),
                    "event_type": "feedback_exposure",
                    "event_version": 1,
                    "session_id": session.pk,
                    "attempt_id": attempt.pk,
                    "client_sequence_no": sequence,
                    "client_wall_time_utc": now.isoformat(),
                    "client_monotonic_ms": sequence * 100,
                    "client_time_origin": "2026-09-06T00:00:00Z",
                    "quality_flags": [],
                    "app_version": "capture-v2-test",
                    "payload": {
                        "capture_version": "capture-v2",
                        "operation_id": visit_id,
                        "feedback_mode": capture["mode"],
                        "stage": stage,
                        "answer_event_id": capture["answer_event_id"],
                        "snapshot_id": capture["snapshot_id"],
                        "included_correctness": flags[0],
                        "included_correct_answer": flags[1],
                        "included_explanation": flags[2],
                    },
                }
            )
        ingest_capture_batch(
            student=self.student,
            document={"client_instance_id": str(stream), "events": events},
        )

        exposures = list(
            AssessmentEvent.objects.filter(
                attempt=attempt,
                event_type="feedback_exposure",
            ).values_list("payload", flat=True)
        )
        self.assertEqual([payload["stage"] for payload in exposures], [row[0] for row in stages])
        self.assertTrue(all(payload["answer_event_id"] == capture["answer_event_id"] for payload in exposures))
        self.assertTrue(all(payload["snapshot_id"] == capture["snapshot_id"] for payload in exposures))

    @override_settings(STUDENT_LEARNING_ANALYTICS_ENABLED=False)
    def test_learning_api_respects_existing_analytics_kill_switch(self):
        self.client.force_login(self.student)

        response = self.client.get(
            f"/api/v1/student/courses/{self.course.pk}/learning/"
        )

        self.assertEqual(response.status_code, 404)


@override_settings(
    DEBUG=True,
    VITE_DEV_SERVER_URL="http://localhost:5173",
    CLASS_CENTER_ENABLED=True,
    STUDENT_LEARNING_ANALYTICS_ENABLED=True,
)
class StudentFrontendEntryTests(StudentApiTestCase):
    def test_course_learning_and_result_pages_use_the_independent_frontend(self):
        self.start()
        session = ExperimentSession.objects.get(
            class_task=self.task,
            student=self.student,
        )
        question_snapshot = self.task.release_snapshot.question_snapshots.get()
        urls = [
            reverse("assessment:dashboard"),
            reverse("assessment:course", args=[self.course.pk]),
            reverse("assessment:learning", args=[self.course.pk]),
            reverse(
                "assessment:learning_experiment",
                args=[self.course.pk, self.experiment.pk],
            ),
            reverse(
                "assessment:learning_question",
                args=[self.course.pk, self.experiment.pk, question_snapshot.pk],
            ),
            reverse("assessment:complete", args=[session.pk]),
        ]

        with patch.dict("os.environ", {"FRONTEND_INTERNAL_REDIRECT": "/__frontend/index.html"}):
            responses = [self.client.get(url) for url in urls]

        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["X-Accel-Redirect"], "/__frontend/index.html")
            self.assertFalse(response.templates)

    def test_assessment_question_uses_the_independent_frontend(self):
        self.start()
        session = ExperimentSession.objects.get(
            class_task=self.task,
            student=self.student,
        )
        assignment = session.assignments.get()

        with patch.dict("os.environ", {"FRONTEND_INTERNAL_REDIRECT": "/__frontend/index.html"}):
            response = self.client.get(
                reverse("assessment:question", args=[session.pk, assignment.pk])
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Accel-Redirect"], "/__frontend/index.html")
        self.assertFalse(response.templates)
