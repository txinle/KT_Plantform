import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import User
from apps.assessment.models import (
    AssessmentEvent, Attempt, ExperimentSession, GradeRecord,
    StudentQuestionAssignment, SubmissionEvent,
)
from apps.assessment.recording_capture_services import ensure_capture_contract_v1
from apps.experiments.models import Experiment, ExperimentVersion, QuestionSnapshot
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.question_bank.models import Question, QuestionBank

from .quality_reports import EvaluationState, build_quality_report
from .models import (
    RecordingCoverageAttempt, RecordingCoverageImmutableQuerySet,
    RecordingCoverageRun, RecordingCoverageSession,
    _recording_coverage_result_append_scope,
)
from .recording_coverage import generate_scope_coverage


class CoverageQueryBudgetTests(TestCase):
    def setUp(self):
        teacher = User.objects.create_user(username="coverage-budget-teacher", role="teacher")
        bank = QuestionBank.objects.create(owner=teacher, name="Coverage budget")
        question = Question.objects.create(
            bank=bank, sequence=1, question_type=Question.Type.SINGLE,
            text="A?", correct_answer="A",
        )
        experiment = Experiment.objects.create(owner=teacher, question_bank=bank, name="Budget")
        version = ExperimentVersion.objects.create(
            experiment=experiment, version_number=1, published_by=teacher,
        )
        snapshot = QuestionSnapshot.objects.create(
            version=version, source_question=question, source_sequence=1, code="Q1",
            question_type=Question.Type.SINGLE, text="A?", correct_answer="A",
        )
        item = get_or_create_default_item_instance(snapshot)
        contract = ensure_capture_contract_v1()
        self.sessions = []
        self.attempts = []
        for index in range(5):
            student = User.objects.create_user(username=f"coverage-budget-{index}", role="student")
            session = ExperimentSession.objects.create(
                version=version, student=student, capture_contract=contract,
            )
            assignment = StudentQuestionAssignment.objects.create(
                session=session, snapshot=snapshot, item_instance=item, assigned_sequence_index=1,
            )
            attempt = Attempt.objects.create(
                session=session, assignment=assignment, status=Attempt.Status.SUBMITTED,
            )
            self.sessions.append(session)
            self.attempts.append(attempt)
            if index == 0:
                continue
            submission = SubmissionEvent.objects.create(
                attempt=attempt, submission_token=uuid.uuid4(),
                grading_status=("pending" if index == 1 else "completed"),
                server_received_at=timezone.now(), committed_at=timezone.now(),
            )
            if index in {2, 3}:
                GradeRecord.objects.create(submission=submission, status="pending")
            if index in {3, 4}:
                GradeRecord.objects.create(
                    submission=submission, version=2, status="completed",
                    validity_status="effective" if index == 3 else "superseded",
                )

    def test_quality_report_reads_all_submissions_in_two_queries(self):
        session_ids = [session.pk for session in self.sessions]
        scope = SimpleNamespace(
            attempts=lambda: Attempt.objects.filter(session_id__in=session_ids).order_by("pk"),
            events=lambda: AssessmentEvent.objects.filter(session_id__in=session_ids),
        )
        with self.assertNumQueries(2):
            report = build_quality_report(scope)

        submissions = [row for row in report.rows if row.rule == "submission"]
        self.assertEqual(len(submissions), 5)
        self.assertEqual(submissions[0].reason_code, "missing_submission")
        self.assertEqual(submissions[0].state, EvaluationState.FAIL)
        self.assertTrue(all(row.state == EvaluationState.PASS for row in submissions[1:]))

    def test_coverage_batches_results_and_grade_state_without_per_attempt_queries(self):
        with CaptureQueriesContext(connection) as queries:
            run = generate_scope_coverage(self.sessions, force=True)

        self.assertLessEqual(len(queries), 10, "Coverage SQL must not grow per attempt/session")
        for table in ("analytics_recordingcoveragesession", "analytics_recordingcoverageattempt"):
            inserts = [query for query in queries if query["sql"].startswith(f'INSERT INTO "{table}"')]
            self.assertEqual(len(inserts), 1, f"Expected one batch insert for {table}")
        outcomes = {
            result.attempt_id: result.analysis_eligibility["outcome"]
            for result in run.attempt_results.all()
        }
        self.assertEqual(run.session_results.count(), 5)
        self.assertEqual(len(outcomes), 5)
        expected = [
            (False, ["submission_chain_mismatch"]),
            (False, ["grading_pending", "effective_grade_unavailable"]),
            (False, ["grading_pending", "effective_grade_unavailable"]),
            (True, []),
            (False, ["effective_grade_unavailable"]),
        ]
        for attempt, (usable, reasons) in zip(self.attempts, expected):
            with self.subTest(attempt=attempt.pk):
                outcome = outcomes[attempt.pk]
                self.assertEqual(outcome["usable"], usable)
                self.assertEqual(outcome["missing_reasons"], sorted(reasons))
                self.assertEqual(outcome["status"], "complete" if usable else "insufficient")

    def test_bulk_results_remain_guarded_and_only_append_to_the_current_run(self):
        run = generate_scope_coverage(self.sessions, force=True)
        result = run.attempt_results.first()
        for model in (RecordingCoverageRun, RecordingCoverageSession, RecordingCoverageAttempt):
            with self.subTest(model=model.__name__), self.assertRaisesMessage(
                ValueError, "created through the coverage service"
            ):
                model.objects.bulk_create([])
        with _recording_coverage_result_append_scope(run):
            for row in (
                result,
                RecordingCoverageAttempt(run_id=uuid.uuid4(), attempt=self.attempts[0]),
                RecordingCoverageAttempt(pk=result.pk, run=run, attempt=self.attempts[0]),
                RecordingCoverageSession(run=run, session=self.sessions[0]),
            ):
                with self.subTest(row=row), self.assertRaisesMessage(ValueError, "only new results"):
                    RecordingCoverageAttempt.objects.bulk_create([row])
            with self.assertRaisesMessage(ValueError, "only new results"):
                RecordingCoverageAttempt.objects.bulk_create([], ignore_conflicts=True)
            with self.assertRaisesMessage(ValueError, "created through the coverage service"):
                RecordingCoverageRun.objects.bulk_create([])
        with self.assertRaisesMessage(ValueError, "created through the coverage service"):
            RecordingCoverageAttempt.objects.bulk_create([])
        self.assertEqual(run.attempt_results.count(), 5)

    def test_failed_attempt_batch_rolls_back_run_and_session_results(self):
        original = RecordingCoverageImmutableQuerySet.bulk_create

        def fail_attempt_batch(queryset, rows, **kwargs):
            if queryset.model is RecordingCoverageAttempt:
                raise ValueError("attempt batch failed")
            return original(queryset, rows, **kwargs)

        with patch.object(
            RecordingCoverageImmutableQuerySet, "bulk_create", autospec=True,
            side_effect=fail_attempt_batch,
        ), self.assertRaisesMessage(ValueError, "attempt batch failed"):
            generate_scope_coverage(self.sessions, force=True)

        self.assertEqual(RecordingCoverageRun.objects.count(), 0)
        self.assertEqual(RecordingCoverageSession.objects.count(), 0)
        self.assertEqual(RecordingCoverageAttempt.objects.count(), 0)
        with self.assertRaisesMessage(ValueError, "created through the coverage service"):
            RecordingCoverageAttempt.objects.bulk_create([])
