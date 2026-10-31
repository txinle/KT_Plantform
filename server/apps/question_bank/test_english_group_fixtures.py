from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from apps.accounts.models import Course, User
from .models import Question, QuestionBank


class EnglishGroupCommandGuardTests(SimpleTestCase):
    def test_e2e_command_refuses_any_non_e2e_database(self):
        with self.assertRaisesMessage(CommandError, 'only runs against kt_e2e'):
            call_command('seed_e2e_english_groups', stdout=StringIO())


class EnglishGroupFixtureTests(TestCase):
    def test_demo_examples_are_idempotent_and_do_not_change_existing_questions(self):
        from .services.example_groups import ensure_english_group_examples
        teacher = User.objects.create_user(username='demo-group-owner', role='teacher')
        original_bank = QuestionBank.objects.create(owner=teacher, name='Original')
        question = Question.objects.create(bank=original_bank, sequence=1, text='Unchanged', question_type='short_answer', correct_answer='a')
        bank = ensure_english_group_examples(teacher)
        self.assertEqual(bank.groups.count(), 3)
        self.assertEqual(bank.questions.count(), 22)
        self.assertEqual(sorted(group.members.count() for group in bank.groups.all()), [3, 5, 5])
        self.assertEqual(bank.questions.filter(status='active').count(), 22)
        self.assertEqual(ensure_english_group_examples(teacher).pk, bank.pk)
        self.assertEqual(bank.questions.count(), 22)
        question.refresh_from_db()
        self.assertEqual((question.text, question.sequence), ('Unchanged', 1))

    def test_e2e_fixture_is_empty_repeatable_owned_and_prepares_real_draft_scopes(self):
        from .management.commands.seed_e2e_english_groups import Command
        unrelated_teacher = User.objects.create_user(username='unrelated-fixture', role='teacher')
        unrelated = Course.objects.create(owner=unrelated_teacher, name='Untouched', code='OTHER', term='2026')
        first = Command()._seed()
        course = Course.objects.get(pk=first['course_id'])
        self.assertEqual(course.owner.username, first['teacher']['username'])
        self.assertTrue(course.concepts.filter(code=first['kc_code'], children__isnull=True).exists())
        self.assertEqual(QuestionBank.objects.get(pk=first['course_bank_id']).questions.count(), 0)
        self.assertIsNone(QuestionBank.objects.get(pk=first['standalone_bank_id']).course_id)
        self.assertEqual(course.experiments.get(pk=first['course_experiment_id']).status, 'draft')
        bank = QuestionBank.objects.get(pk=first['course_bank_id'])
        Question.objects.create(bank=bank, sequence=1, question_type='short_answer', text='Reset owned fixture', correct_answer='a')
        second = Command()._seed()
        self.assertEqual(second['teacher'], first['teacher'])
        self.assertEqual(second['student'], first['student'])
        self.assertEqual(QuestionBank.objects.get(pk=second['course_bank_id']).questions.count(), 0)
        self.assertTrue(Course.objects.filter(pk=unrelated.pk, name='Untouched').exists())

    def test_e2e_reset_removes_its_published_group_and_task_dependency_closure(self):
        import uuid
        from apps.assessment.models import AssessmentEvent, Attempt, ExperimentSession, StudentQuestionAssignment
        from apps.assessment.event_services import append_assessment_event
        from .management.commands.seed_e2e_english_groups import Command
        from .services.question_groups import save_question_group
        from apps.experiments.models import Experiment, QuestionGroupSnapshot, QuestionSnapshot
        from apps.experiments.services import sync_experiment_questions
        from apps.experiments.publication_services import get_or_create_publish_plan, save_plan_selection, generate_preview, commit_publish_plan
        fixture = Command()._seed()
        bank = QuestionBank.objects.get(pk=fixture['course_bank_id'])
        save_question_group(bank=bank, data={'kind': 'cloze', 'material': 'At {{b01}}.', 'members': [{
            'group_order': 1, 'blank_id': 'b01', 'question_type': 'fill_blank', 'text': '', 'answer': 'home',
            'kc_mapping': {'root_kc_code': fixture['kc_code'], 'nodes': [{'kc_code': fixture['kc_code'], 'parent_kc_code': None, 'order': 1}]}}]})
        experiment = Experiment.objects.get(pk=fixture['course_experiment_id'])
        teacher = experiment.owner
        sync_experiment_questions(experiment)
        plan = get_or_create_publish_plan(experiment, teacher)
        plan = save_plan_selection(plan, teacher, expected_revision=plan.plan_revision, class_ids=[fixture['class_id']], exclusions_by_class={})
        generate_preview(plan, teacher, expected_revision=plan.plan_revision)
        plan.refresh_from_db()
        commit_publish_plan(plan, teacher, expected_revision=plan.plan_revision)
        self.assertTrue(QuestionGroupSnapshot.objects.filter(version__experiment=experiment).exists())
        snapshot = QuestionSnapshot.objects.get(version__experiment=experiment)
        session = ExperimentSession.objects.get(version=snapshot.version, student_id=fixture['student_id'])
        assignment = StudentQuestionAssignment.objects.get(session=session, snapshot=snapshot)
        attempt, _ = Attempt.objects.get_or_create(session=session, assignment=assignment)
        event = append_assessment_event(event_type='item_entered', event_version=1, event_domain='assessment', actor_source='client',
            session=session, assignment=assignment, attempt=attempt, question_snapshot=snapshot, item_instance=assignment.item_instance,
            payload={'entry_reason': 'session_start', 'from_assignment_id': None}, client_instance_id=uuid.uuid4(), client_sequence_no=1)
        Attempt.objects.filter(pk=attempt.pk).update(active_response_event=event, final_response_event=event)
        reset = Command()._seed()
        self.assertFalse(Experiment.objects.filter(pk=experiment.pk).exists())
        self.assertFalse(AssessmentEvent.objects.filter(pk=event.pk).exists())
        self.assertEqual(QuestionBank.objects.get(pk=reset['course_bank_id']).questions.count(), 0)
