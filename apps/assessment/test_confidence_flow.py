import json
import uuid
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.experiments.models import Experiment, ExperimentQuestion, ExperimentStudent
from apps.experiments.services import publish_experiment
from apps.question_bank.models import (
    Concept,
    Question,
    QuestionBank,
    QuestionConcept,
    QuestionOption,
)

from .event_services import append_assessment_event
from .models import AssessmentEvent, ConfidenceAssignment
from .services import get_or_create_session


class ConfidenceFlowTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="confidence-teacher",
            role=User.Role.TEACHER,
        )
        self.student = User.objects.create_user(
            username="CONFIDENCE-S1",
            student_number="CONFIDENCE-S1",
            role=User.Role.STUDENT,
        )
        bank = QuestionBank.objects.create(owner=self.teacher, name="Confidence 题库")
        concept = Concept.objects.create(
            owner=self.teacher,
            scope=Concept.Scope.PRIVATE,
            name="Confidence KC",
            normalized_name="confidence kc",
        )
        self.experiment = Experiment.objects.create(
            owner=self.teacher,
            question_bank=bank,
            name="Confidence 实验",
            feedback_mode=Experiment.Feedback.FULL,
            confidence_mode=Experiment.ConfidenceMode.ALL,
            confidence_sampling_unit=Experiment.ConfidenceSamplingUnit.ATTEMPT,
            confidence_randomization_seed="confidence-fixed-seed",
            confidence_assignment_version="assign-v1",
            confidence_scale_version="v2",
            confidence_prompt_version="prompt-v3",
        )
        for sequence in range(1, 5):
            question = Question.objects.create(
                bank=bank,
                sequence=sequence,
                question_type=Question.Type.SINGLE,
                text=f"题目 {sequence}",
                correct_answer="A",
            )
            QuestionOption.objects.create(question=question, key="A", text="正确")
            QuestionOption.objects.create(question=question, key="B", text="错误")
            QuestionConcept.objects.create(
                question=question,
                concept=concept,
                is_primary=True,
            )
            ExperimentQuestion.objects.create(
                experiment=self.experiment,
                question=question,
                display_order=sequence,
            )
        ExperimentStudent.objects.create(
            experiment=self.experiment,
            student=self.student,
        )
        publish_experiment(self.experiment, self.teacher)
        self.experiment.refresh_from_db()
        self.session = get_or_create_session(self.experiment, self.student)
        self.client.force_login(self.student)

    def submit_assignment(self, assignment, confidence=None):
        draft = self.client.post(
            reverse("assessment:save_draft", args=[assignment.attempt.pk]),
            data=json.dumps({"answer": "A"}),
            content_type="application/json",
        )
        self.assertEqual(draft.status_code, 200)
        return self.client.post(
            reverse("assessment:submit", args=[assignment.attempt.pk]),
            data=json.dumps({"submission_token": str(uuid.uuid4()), **({"confidence": confidence} if confidence else {})}),
            content_type="application/json",
        )

    def post_process(self, name, attempt, payload=None):
        return self.client.post(
            reverse(f"assessment_process:{name}", args=[attempt.pk]),
            data=json.dumps(payload or {}),
            content_type="application/json",
        )

    def test_every_attempt_has_an_explicit_frozen_assignment(self):
        assignments = list(
            ConfidenceAssignment.objects.filter(attempt__session=self.session)
            .select_related("attempt")
            .order_by("attempt_id")
        )

        self.assertEqual(len(assignments), self.session.attempts.count())
        self.assertTrue(
            all(item.condition == ConfidenceAssignment.Condition.PROMPT for item in assignments)
        )
        self.assertTrue(
            all(item.response_status == ConfidenceAssignment.ResponseStatus.ASSIGNED for item in assignments)
        )
        self.assertTrue(
            all(item.assignment_version == "assign-v1" for item in assignments)
        )


    def test_prompt_is_available_before_submission_and_feedback_follows_final_confirmation(self):
        assignment = self.session.assignments.order_by("assigned_sequence_index").first()
        entry = self.client.get(f'/api/v1/student/sessions/{self.session.pk}/questions/{assignment.pk}/')
        contract = entry.json()['pending_confidence']
        self.assertEqual(contract['scale_version'], 'v2')
        self.assertEqual(contract['step'], 1)
        self.assertEqual(contract['prompt_version'], 'prompt-v3')
        rejected = self.submit_assignment(assignment)
        self.assertEqual(rejected.status_code, 400)
        self.assertNotIn('correct_answer', rejected.json())
        shown = self.post_process('confidence_prompt_shown', assignment.attempt)
        self.assertEqual(shown.status_code, 200)
        self.post_process('confidence_prompt_shown', assignment.attempt)
        self.assertEqual(AssessmentEvent.objects.filter(attempt=assignment.attempt, event_type='confidence_prompt_shown').count(), 1)
        submitted = self.submit_assignment(assignment, {'status': 'committed', 'probability': 80})
        self.assertEqual(submitted.status_code, 200)
        self.assertFalse(submitted.json()['confidence_required'])
        self.assertEqual(submitted.json()['correct_answer'], 'A')
        confidence = ConfidenceAssignment.objects.get(attempt=assignment.attempt)
        self.assertEqual(confidence.response_status, 'committed')
        self.assertEqual(confidence.probability, 80)
        self.assertEqual(
            AssessmentEvent.objects.get(
                attempt=assignment.attempt,
                event_type="confidence_committed",
            ).event_version,
            2,
        )

    def test_frozen_scale_version_controls_probability_validation(self):
        from .confidence_services import ConfidenceFlowError, validate_confidence_probability

        attempt = self.session.attempts.order_by("pk").first()
        for probability in (0, 1, 57, 100):
            self.assertEqual(validate_confidence_probability(attempt, probability), probability)
        for probability in (-1, 57.5, 101, True):
            with self.assertRaises(ConfidenceFlowError):
                validate_confidence_probability(attempt, probability)

        configuration = self.session.version.configuration
        configuration["confidence_scale_version"] = "v1"
        self.session.version.configuration = configuration
        self.session.version.save(update_fields=["configuration"])
        attempt.session.version = self.session.version

        for probability in range(0, 101, 10):
            self.assertEqual(validate_confidence_probability(attempt, probability), probability)
        for probability in (1, 57, 57.5, 101):
            with self.assertRaises(ConfidenceFlowError):
                validate_confidence_probability(attempt, probability)

    def test_prompt_may_be_skipped_but_commit_and_skip_are_mutually_exclusive(self):
        assignment = self.session.assignments.order_by("assigned_sequence_index")[1]
        self.post_process('confidence_prompt_shown', assignment.attempt)
        self.assertEqual(self.submit_assignment(assignment, {'status': 'skipped'}).status_code, 200)
        confidence = ConfidenceAssignment.objects.get(attempt=assignment.attempt)
        self.assertEqual(confidence.response_status, 'skipped')
        self.assertEqual(AssessmentEvent.objects.filter(attempt=assignment.attempt, event_type='confidence_skipped').count(), 1)
        self.assertEqual(self.post_process('confidence_commit', assignment.attempt, {'probability': 70}).status_code, 409)

    def test_feedback_event_is_rejected_until_prompt_assignment_is_resolved(self):
        assignment = self.session.assignments.order_by("assigned_sequence_index")[2]
        self.assertEqual(self.submit_assignment(assignment).status_code, 400)
        attempt = assignment.attempt
        attempt.refresh_from_db()
        payload = {
            "feedback_mode": "full",
            "included_correctness": True,
            "included_correct_answer": True,
            "included_explanation": True,
        }

        with self.assertRaises(ValueError):
            append_assessment_event(
                event_type="feedback_shown",
                event_version=1,
                event_domain="assessment",
                actor_source="client",
                session=attempt.session,
                attempt=attempt,
                payload=payload,
                app_version="test",
            )

        self.post_process("confidence_prompt_shown", attempt)
        self.assertEqual(self.submit_assignment(assignment, {"status": "committed", "probability": 60}).status_code, 200)
        event = append_assessment_event(
            event_type="feedback_shown",
            event_version=1,
            event_domain="assessment",
            actor_source="client",
            session=attempt.session,
            attempt=attempt,
            payload=payload,
            app_version="test",
        )
        self.assertEqual(event.event_type, "feedback_shown")

    def test_confidence_event_cannot_outrun_its_projection(self):
        assignment = self.session.assignments.order_by("assigned_sequence_index").last()
        confidence = ConfidenceAssignment.objects.get(attempt=assignment.attempt)

        with self.assertRaises(ValueError):
            append_assessment_event(
                event_type="confidence_committed",
                event_version=1,
                event_domain="assessment",
                actor_source="server",
                session=assignment.session,
                attempt=assignment.attempt,
                payload={
                    "confidence_assignment_id": str(confidence.pk),
                    "probability": 90,
                },
                app_version="server",
            )

        self.assertFalse(
            AssessmentEvent.objects.filter(
                attempt=assignment.attempt,
                event_type="confidence_committed",
            ).exists()
        )

    def test_sampled_assignments_are_deterministic_at_each_configured_unit(self):
        from .confidence_services import (
            confidence_assignment_score,
            materialize_confidence_assignments,
        )

        ConfidenceAssignment.objects.filter(attempt__session=self.session).delete()
        configuration = self.session.version.configuration
        configuration.update(
            {
                "confidence_mode": "sampled",
                "confidence_sampling_unit": "student",
                "confidence_probability": "0.5",
            }
        )
        self.session.version.configuration = configuration
        self.session.version.save(update_fields=["configuration"])
        student_assignments = materialize_confidence_assignments(self.session)
        self.assertEqual(len({item.condition for item in student_assignments}), 1)

        ConfidenceAssignment.objects.filter(attempt__session=self.session).delete()
        configuration["confidence_sampling_unit"] = "attempt"
        scores = [
            confidence_assignment_score(attempt, configuration)
            for attempt in self.session.attempts.order_by("pk")
        ]
        configuration["confidence_probability"] = str(
            (min(scores) + max(scores)) / Decimal("2")
        )
        self.session.version.configuration = configuration
        self.session.version.save(update_fields=["configuration"])
        attempt_assignments = materialize_confidence_assignments(self.session)
        self.assertEqual(
            {item.condition for item in attempt_assignments},
            {
                ConfidenceAssignment.Condition.CONTROL,
                ConfidenceAssignment.Condition.PROMPT,
            },
        )

        original = {
            item.attempt_id: item.condition for item in attempt_assignments
        }
        ConfidenceAssignment.objects.filter(attempt__session=self.session).delete()
        replay = {
            item.attempt_id: item.condition
            for item in materialize_confidence_assignments(self.session)
        }
        self.assertEqual(replay, original)

    def test_control_assignment_preserves_immediate_frozen_feedback(self):
        other_student = User.objects.create_user(
            username="CONFIDENCE-S2",
            student_number="CONFIDENCE-S2",
            role=User.Role.STUDENT,
        )
        ExperimentStudent.objects.create(
            experiment=self.experiment,
            student=other_student,
        )
        configuration = self.session.version.configuration
        configuration.update(
            {
                "confidence_mode": "sampled",
                "confidence_probability": "0",
            }
        )
        self.session.version.configuration = configuration
        self.session.version.save(update_fields=["configuration"])
        other_session = get_or_create_session(self.experiment, other_student)
        self.client.force_login(other_student)
        assignment = other_session.assignments.order_by("assigned_sequence_index").first()

        submitted = self.submit_assignment(assignment)

        self.assertEqual(submitted.status_code, 200)
        data = submitted.json()
        self.assertFalse(data["confidence_required"])
        self.assertTrue(data["is_correct"])
        self.assertEqual(data["correct_answer"], "A")
