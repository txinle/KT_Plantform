import uuid
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone

from apps.accounts.models import CourseEnrollment
from apps.api.test_support import domain_payload
from apps.assessment.models import Attempt, ExperimentSession, GradeRecord, StudentQuestionAssignment, SubmissionEvent
from apps.assessment.test_student_api import StudentApiFixtureMixin
from apps.experiments.models import QuestionGroupSnapshot, QuestionSnapshot
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.question_bank.models import Question, QuestionGroup


class GroupResultContentTests(SimpleTestCase):
    def test_missing_effective_numeric_score_is_pending_not_zero(self):
        from apps.assessment.student_results import _score_summary, score_progress
        attempt = SimpleNamespace(pk=1, assignment=SimpleNamespace(snapshot=SimpleNamespace(max_score=Decimal('2.50'))))
        grades = {1: SimpleNamespace(score=None, max_score=Decimal('2.50'))}
        self.assertIsNone(_score_summary([attempt], {1: object()}, grades))
        self.assertEqual(score_progress([attempt], {1: object()}, grades)['pending_count'], 1)

    def test_reading_group_options_use_public_presentation_without_answer_markers(self):
        from apps.assessment.student_results import result_groups
        group = SimpleNamespace(pk=1, source_group_id=7, kind='reading', title='Reading', instructions='', material='A passage.', content_language='en')
        question = SimpleNamespace(pk=2, group_snapshot_id=1, group_snapshot=group, group_order=1, blank_id='', max_score=Decimal('2.50'),
            question_type='single_choice', text='Which one?', options=[{'key': 'A', 'text': 'First'}, {'key': 'B', 'text': 'Second'}])
        attempt = SimpleNamespace(pk=3, assignment=SimpleNamespace(snapshot=question))
        result = result_groups([attempt], [], {}, {}, show_scores=False)
        self.assertEqual(result[0]['members'][0]['options'], [{'key': 'A', 'text': 'First'}, {'key': 'B', 'text': 'Second'}])
        self.assertNotIn('max_score', result[0]['members'][0])


@override_settings(STUDENT_SHARED_RESULTS_ENABLED=True, STUDENT_SHARED_RESULTS_COURSE_IDS=frozenset({'*'}),
                   CLASS_CENTER_ENABLED=True, STUDENT_LEARNING_ANALYTICS_ENABLED=True)
class GroupStudentResultsTests(StudentApiFixtureMixin, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.session = ExperimentSession.objects.get(pk=domain_payload(self.start())['session_id'])
        self.complete_session(self.session)
        source = QuestionGroup.objects.create(bank=self.bank, kind='cloze', title='A visit', material='Go {{b01}} by {{b02}}.')
        self.group = QuestionGroupSnapshot.objects.create(version=self.session.version, source_group_id=source.pk, source_revision=1,
            kind='cloze', title=source.title, material=source.material, content_language='en')
        self.group_attempts = []
        for order, (answer, maximum) in enumerate([('home', '2.50'), ('bus', '4.50')], 1):
            question = Question.objects.create(bank=self.bank, group=source, group_order=order, blank_id=f'b{order:02}',
                sequence=order + 1, question_type='fill_blank', text='', correct_answer=answer, max_score=maximum)
            snapshot = QuestionSnapshot.objects.create(version=self.session.version, source_question=question, group_snapshot=self.group, group_order=order,
                blank_id=f'b{order:02}', source_sequence=order + 1, code=f'GROUP-{order}', question_type='fill_blank',
                text='', correct_answer=answer, explanation=f'Hidden explanation {order}', max_score=maximum, content_language='en')
            assignment = StudentQuestionAssignment.objects.create(session=self.session, snapshot=snapshot,
                item_instance=get_or_create_default_item_instance(snapshot), assigned_sequence_index=order + 1)
            attempt = Attempt.objects.create(session=self.session, assignment=assignment, status='submitted')
            submission = SubmissionEvent.objects.create(attempt=attempt, submission_token=uuid.uuid4(), final_answer=answer,
                server_received_at=timezone.now(), committed_at=timezone.now(), grading_status='completed' if order == 1 else 'pending')
            if order == 1:
                GradeRecord.objects.create(submission=submission, status='completed', validity_status='effective', is_correct=True,
                    score=Decimal(maximum), max_score=Decimal(maximum))
            self.group_attempts.append(attempt)

    def test_pending_scores_separate_full_paper_and_graded_denominators_and_group_result(self):
        from apps.assessment.student_results import build_student_task_result
        result = build_student_task_result(self.task, self.student)
        self.assertNotIn('score', result)
        progress = result['score_progress']
        self.assertEqual(progress['paper_maximum'], Decimal('8.00'))
        self.assertEqual(progress['graded_maximum'], Decimal('3.50'))
        self.assertEqual(progress['pending_count'], 1)
        self.assertEqual(progress['earned'], Decimal('3.50'))
        group = result['groups'][0]
        self.assertEqual(group['score_progress']['paper_maximum'], Decimal('7.00'))
        self.assertEqual(group['score_progress']['graded_maximum'], Decimal('2.50'))
        self.assertEqual(group['material_segments'][1]['item_key'], group['members'][0]['item_key'])
        self.assertEqual(group['members'][0]['response_value'], 'home')
        self.assertNotIn('correct_answer', group['members'][1])
        self.assertNotIn('explanation', group['members'][1])

    def test_analytics_result_preserves_weighted_progress_and_group_metadata(self):
        enrollment = CourseEnrollment.objects.get(course=self.course, student=self.student)
        from .teaching.student_projection import build_student_experiment_projection, student_projection_to_api
        projection = student_projection_to_api(build_student_experiment_projection(enrollment, experiment_id=self.experiment.pk, require_results=True))
        self.assertEqual(projection['score_progress']['paper_maximum'], Decimal('8.00'))
        self.assertEqual(projection['groups'][0]['group_id'], str(self.group.pk))
        response = self.client.get(f'/api/v1/student/courses/{self.course.pk}/learning/experiments/{self.experiment.pk}/')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Decimal(domain_payload(response)['score_progress']['paper_maximum']), Decimal('8.00'))
        self.assertEqual(domain_payload(response)['groups'][0]['group_id'], str(self.group.pk))

    def test_score_item_and_feedback_policies_remove_group_and_score_branches(self):
        from apps.assessment.student_results import build_student_task_result
        policy = self.class_group.student_analytics_policy
        policy.show_scores = False
        policy.show_item_results = False
        policy.save()
        result = build_student_task_result(self.task, self.student)
        self.assertNotIn('score_progress', result)
        self.assertNotIn('groups', result)
        self.session.status = 'in_progress'
        self.session.terminal_at = None
        self.session.save(update_fields=['status', 'terminal_at'])
        result = build_student_task_result(self.task, self.student)
        self.assertNotIn('score_progress', result)
        self.assertNotIn('groups', result)
