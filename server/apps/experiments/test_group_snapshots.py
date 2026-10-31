from decimal import Decimal
import json
import uuid

from django.test import TestCase

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept, QuestionGroup, QuestionOption
from .models import Experiment, ExperimentSamplingKC, ExperimentStudent
from .services import ordered_snapshots, publish_experiment


class GroupSnapshotTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username='group-publisher', role='teacher')
        self.student = User.objects.create_user(username='group-student', role='student', student_number='group-student')
        self.course = Course.objects.create(owner=self.teacher, code='GROUP-EN', name='English', term='2026')
        self.class_group = ClassGroup.objects.create(course=self.course, owner=self.teacher, name='English class', academic_year='2026')
        self.student.class_group = self.class_group
        self.student.save(update_fields=['class_group'])
        CourseEnrollment.objects.create(course=self.course, student=self.student, class_group=self.class_group, is_active=True)
        self.bank = QuestionBank.objects.create(owner=self.teacher, course=self.course, name='English')
        self.kc = Concept.objects.create(course=self.course, code='KC1', name='Reading')
        self.group = QuestionGroup.objects.create(bank=self.bank, kind='reading', title='Garden', material='A frozen garden.')
        for order in range(1, 4):
            question = Question.objects.create(bank=self.bank, sequence=order, group=self.group, group_order=order,
                question_type='single_choice', text=f'Why {order}?', correct_answer='A', max_score=Decimal('2.00'))
            QuestionOption.objects.create(question=question, key='A', text='Because')
            QuestionOption.objects.create(question=question, key='B', text='Otherwise')
            QuestionConcept.objects.create(question=question, concept=self.kc, is_primary=True)
        single = Question.objects.create(bank=self.bank, sequence=4, question_type='fill_blank', text='A word', correct_answer='London',
            max_score=Decimal('3.50'), text_match_case_sensitive=True)
        QuestionConcept.objects.create(question=single, concept=self.kc, is_primary=True)

    def publish(self, **changes):
        from .publication_services import get_or_create_publish_plan, save_plan_selection, generate_preview, commit_publish_plan
        values = {'course': self.course, 'owner': self.teacher, 'created_by': self.teacher, 'question_bank': self.bank,
            'name': 'English groups', 'selection_mode': 'all', 'sampling_mode': None, **changes}
        if not self.bank.questions.filter(group__isnull=False).exists():
            values.update(sampling_mode=Experiment.SamplingMode.TOTAL_COUNT, sampling_total_count=self.bank.questions.count())
        experiment = Experiment.objects.create(**values)
        if experiment.sampling_mode:
            ExperimentSamplingKC.objects.create(experiment=experiment, concept=self.kc)
        plan = get_or_create_publish_plan(experiment, self.teacher)
        plan = save_plan_selection(plan, self.teacher, expected_revision=plan.plan_revision,
            class_ids=[self.class_group.pk], exclusions_by_class={})
        generate_preview(plan, self.teacher, expected_revision=plan.plan_revision)
        plan.refresh_from_db()
        commit_publish_plan(plan, self.teacher, expected_revision=plan.plan_revision)
        experiment.refresh_from_db()
        return experiment, experiment.versions.order_by('-version_number').first()

    def start_session(self, experiment):
        from apps.api.test_support import domain_payload
        from apps.assessment.models import ExperimentSession
        from apps.assessment.test_student_api import StudentApiFixtureMixin
        response = StudentApiFixtureMixin.start(self, task=experiment.class_tasks.get())
        self.assertEqual(response.status_code, 201, response.content)
        self.entry_runtime = domain_payload(response)['runtime']
        return ExperimentSession.objects.get(pk=domain_payload(response)['session_id'])

    def test_freezes_group_material_and_scoring_rules(self):
        experiment, version = self.publish()
        snapshots = version.question_snapshots.order_by('source_sequence')
        self.assertEqual([row.max_score for row in snapshots], [Decimal('2.00')] * 3 + [Decimal('3.50')])
        self.assertTrue(snapshots.last().text_match_case_sensitive)
        frozen = snapshots.first().group_snapshot
        self.group.material = 'Changed later'
        self.group.save()
        frozen.refresh_from_db()
        self.assertEqual(frozen.material, 'A frozen garden.')
        self.assertEqual(frozen.members.count(), 3)

    def test_teacher_can_create_and_edit_a_full_group_pool_through_the_course_api(self):
        from apps.api.test_support import domain_payload
        self.client.force_login(self.teacher)
        url = f'/api/v1/teacher/courses/{self.course.pk}/experiments/'
        response = self.client.post(url, {'name': 'Course group draft', 'question_bank_ids': [self.bank.pk],
            'sampling': {'mode': None, 'total_count': None, 'match_scope': 'any_level', 'nodes': [], 'type_quotas': []}}, content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        experiment_id = domain_payload(response)['id']
        response = self.client.patch(f'{url}{experiment_id}/', {'name': 'Edited group draft'}, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        readiness = self.client.get(f'{url}{experiment_id}/readiness/')
        self.assertTrue(domain_payload(readiness)['question_group_pool'])
        self.assertEqual(self.client.post(f'{url}{experiment_id}/publish-plans/', {}, content_type='application/json').status_code, 201)

    def test_standalone_group_publication_is_rejected_before_a_version_is_created(self):
        from django.core.exceptions import ValidationError
        from apps.question_bank.services.question_groups import save_question_group
        bank = QuestionBank.objects.create(owner=self.teacher, name='Unbound group bank')
        concept = Concept.objects.create(owner=self.teacher, scope='private', name='Unbound vocabulary', normalized_name='unbound vocabulary')
        save_question_group(bank=bank, data={'kind': 'cloze', 'material': 'At {{b01}}.', 'members': [{
            'group_order': 1, 'blank_id': 'b01', 'question_type': 'fill_blank', 'text': '', 'answer': 'home',
            'primary_concept_id': concept.pk}]})
        experiment = Experiment.objects.create(owner=self.teacher, question_bank=bank, name='Must use course')
        ExperimentStudent.objects.create(experiment=experiment, student=self.student)
        with self.assertRaisesMessage(ValidationError, '课程实验'):
            publish_experiment(experiment, self.teacher)
        self.assertFalse(experiment.versions.exists())

    def test_randomizes_units_without_splitting_or_reordering_members(self):
        experiment, version = self.publish(ordering_mode='random')
        for seed in range(5):
            ordered = ordered_snapshots(version, self.student.pk, ordering_seed=f'{seed:064x}')
            positions = [index for index, row in enumerate(ordered) if row.source_question.group_id]
            self.assertEqual(positions, list(range(positions[0], positions[0] + 3)))
            self.assertEqual([ordered[index].group_order for index in positions], [1, 2, 3])

    def test_submission_uses_frozen_case_and_max_score(self):
        from apps.assessment.session_submission_services import claim_session_submission, run_session_submission
        from django.urls import reverse
        from django.utils import timezone

        self.bank.questions.filter(group=self.group).update(group=None, group_order=None)
        self.bank.questions.exclude(question_type='fill_blank').delete()
        experiment, version = self.publish()
        session = self.start_session(experiment)
        assignment = session.assignments.get(snapshot__question_type='fill_blank')
        self.client.force_login(self.student)
        from apps.assessment.test_capture_v2 import CaptureV2Tests
        lease = self.entry_runtime['initial_writer_lease']
        self.session, self.stream = session, uuid.UUID(lease['writer_client_instance_id'])
        event = CaptureV2Tests.transition(self, assignment.attempt, '', 'London')
        CaptureV2Tests.ingest(self, [event])
        saved = self.client.post(reverse('assessment_api:save_draft', args=[assignment.attempt.pk]),
            json.dumps({'answer': 'London', 'target_event_id': event['event_id'], 'expected_version': 0,
                'writer_lease_token': lease['writer_lease_token'], 'writer_client_instance_id': lease['writer_client_instance_id']}),
            content_type='application/json')
        self.assertEqual(saved.status_code, 200, saved.content)
        document = {'submission_token': str(uuid.uuid4()), 'client_clicked_at': timezone.now().isoformat(),
            'expected_versions': [{'attempt_id': row.pk, 'response_version': row.response_version,
                'target_event_id': str(row.active_response_event_id) if row.active_response_event_id else None}
                for row in session.attempts.order_by('pk')]}
        response = self.client.post(reverse('assessment_api:finish', args=[session.pk]), json.dumps(document), content_type='application/json')
        self.assertEqual(response.status_code, 202, response.content)
        run_session_submission(claim_session_submission(worker_id='group-snapshot-test'))
        from apps.assessment.models import GradeRecord
        grade = GradeRecord.objects.get(submission__attempt_id=assignment.attempt.pk)
        self.assertEqual(grade.score, Decimal('3.50'))
        self.assertEqual(grade.max_score, Decimal('3.50'))

    def test_course_preview_orders_complete_units(self):
        from .publication_services import _select_questions
        experiment = Experiment.objects.create(course=self.course, owner=self.teacher, question_bank=self.bank, name='Preview', ordering_mode='random')
        candidates = list(self.bank.questions.prefetch_related('concept_links__concept'))
        for seed in range(5):
            rows = _select_questions(experiment, candidates, f'{seed:016x}', f'{seed:016x}')
            positions = [index for index, row in enumerate(rows) if row['candidate'].group_id]
            self.assertEqual(positions, list(range(positions[0], positions[0] + 3)))
            self.assertEqual([rows[index]['candidate'].group_order for index in positions], [1, 2, 3])

    def test_material_and_score_change_invalidates_release_fingerprint(self):
        from .publication_services import question_release_fingerprint
        question = self.group.members.first()
        before = question_release_fingerprint(question)
        self.group.material = 'New material'
        self.group.save()
        question.refresh_from_db()
        self.assertNotEqual(before, question_release_fingerprint(question))

    def test_student_group_projection_uses_frozen_members_without_creating_visits(self):
        from apps.assessment.group_runtime import group_runtime_projection
        experiment, _ = self.publish()
        session = self.start_session(experiment)
        attempt = session.attempts.filter(assignment__snapshot__group_snapshot__isnull=False).first()
        before = session.visit_event_count
        projection = group_runtime_projection(attempt)
        self.assertEqual(len(projection['question_group']['members']), 3)
        self.assertNotIn('correct_answer', json.dumps(projection))
        self.assertNotIn('explanation', json.dumps(projection))
        self.assertEqual(projection['question_group']['material'], 'A frozen garden.')
        session.refresh_from_db()
        self.assertEqual(session.visit_event_count, before)

    def test_new_group_session_binds_prepared_paper_capability(self):
        from apps.assessment.prepared_models import PreparedSessionArtifact
        experiment, _ = self.publish()
        session = self.start_session(experiment)
        self.assertEqual(session.assessment_flow_version, 1)
        self.assertEqual(session.transition_protocol_version, 1)
        artifact = PreparedSessionArtifact.objects.get(session=session)
        self.assertTrue(artifact.ready)
        self.assertEqual(artifact.first_runtime['prepared_paper']['version'], 2)
