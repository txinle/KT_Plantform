import csv
import json
import os
from io import StringIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.accounts.research_consent import CURRENT_RESEARCH_CONSENT_VERSION, accept_current_research_consent
from apps.assessment.models import StudentQuestionAssignment
from apps.assessment.response_snapshot_services import canonical_choice_response_state
from apps.experiments.models import Experiment
from apps.experiments.publication_services import commit_publish_plan, generate_preview, get_or_create_publish_plan, save_plan_selection
from apps.experiments.services import assign_students, sync_experiment_questions
from apps.question_bank.models import Concept, QuestionBank
from apps.question_bank.services.import_templates import question_group_template_rows
from apps.question_bank.services.unified_import import UnifiedQuestionImportService
from apps.question_bank.services.unified_reader import GROUP_HEADERS
from .seed_e2e_english_groups import Command as EnglishGroupFixture


ESSAY = (
    'I suggest a monthly reading exchange at our school. Each student would bring a favourite book '
    'and prepare a short introduction. We would meet in small groups in the library on Friday '
    'afternoons. Everyone would have time to explain the story and answer questions. After the '
    'discussion, students could borrow a book from a partner and write a short recommendation. '
    'This activity would be useful for two reasons. First, we could discover books that we might '
    'never choose by ourselves. Second, talking about stories would help us express our ideas '
    'clearly and listen carefully to other people. Teachers could join the groups and help us '
    'when a conversation becomes difficult. I believe this simple activity would make reading '
    'more enjoyable for everyone.'
)


class Command(BaseCommand):
    help = 'Publish the dedicated Site v2 eleven-type student fixture only in kt_e2e.'

    def handle(self, *args, **options):
        if os.environ.get('POSTGRES_DB') != 'kt_e2e' or str(connection.settings_dict['NAME']) != 'kt_e2e':
            raise CommandError('seed_e2e_site_v2_all_types only runs against kt_e2e with POSTGRES_DB=kt_e2e.')
        self.stdout.write(json.dumps(self._seed(), ensure_ascii=False, sort_keys=True))

    @transaction.atomic
    def _seed(self):
        teacher = self._user('site-v2-types-teacher', 'SiteV2Teacher123!', User.Role.TEACHER)
        student = self._user('site-v2-types-student', 'SiteV2Student123!', User.Role.STUDENT)
        course, _ = Course.objects.get_or_create(owner=teacher, code='SITE-V2-TYPES-E2E', term='2026', defaults={'name': 'Site v2 all question types'})
        course.status = Course.Status.ACTIVE
        course.save(update_fields=['status'])
        EnglishGroupFixture._reset_owned(course, teacher)
        class_group, _ = ClassGroup.objects.get_or_create(course=course, name='Site v2 types class', academic_year='2026', defaults={'owner': teacher})
        student.class_group = class_group
        student.save(update_fields=['class_group'])
        CourseEnrollment.objects.update_or_create(course=course, student=student, defaults={'class_group': class_group, 'is_active': True})
        accept_current_research_consent(student=student, version=CURRENT_RESEARCH_CONSENT_VERSION)
        Concept.objects.get_or_create(course=course, code='KC1', defaults={'name': 'Site v2 example knowledge'})
        bank = QuestionBank.objects.create(course=course, owner=teacher, name='Site v2 eleven-type fixture')
        output = StringIO(newline='')
        writer = csv.DictWriter(output, fieldnames=GROUP_HEADERS)
        writer.writeheader()
        writer.writerows({**row, '一级知识点': 'KC1'} for row in question_group_template_rows())
        arguments = {'bank': bank, 'actor': teacher, 'session_key': 'site-v2-all-types-e2e'}
        preview = UnifiedQuestionImportService.preview(**arguments, uploaded=SimpleUploadedFile('site-v2-types.csv', output.getvalue().encode('utf-8-sig')))
        if preview['preview']['summary'] != {'ready': 22, 'draft': 0, 'duplicate': 0, 'invalid': 0}:
            raise CommandError(f'Unexpected all-types fixture preview: {preview["preview"]["summary"]}')
        UnifiedQuestionImportService.confirm(**arguments, preview_id=preview['preview_id'])
        experiment = Experiment.objects.create(course=course, owner=teacher, created_by=teacher, question_bank=bank,
            name='Site v2 all question types', selection_mode=Experiment.SelectionMode.ALL, sampling_mode=None,
            ordering_mode=Experiment.Ordering.FIXED, feedback_mode=Experiment.Feedback.FULL,
            feedback_release_policy=Experiment.FeedbackReleasePolicy.AFTER_PERSONAL_SUBMISSION,
            confidence_mode=Experiment.ConfidenceMode.ALL, resume_mode=Experiment.ResumeMode.RESUMABLE)
        sync_experiment_questions(experiment)
        assign_students(experiment, [student])
        plan = get_or_create_publish_plan(experiment, teacher)
        plan = save_plan_selection(plan, teacher, expected_revision=plan.plan_revision, class_ids=[class_group.pk], exclusions_by_class={})
        generate_preview(plan, teacher, expected_revision=plan.plan_revision)
        plan.refresh_from_db()
        task = commit_publish_plan(plan, teacher, expected_revision=plan.plan_revision)[0]
        assignments = StudentQuestionAssignment.objects.filter(session__class_task=task, session__student=student).select_related('snapshot__source_question__group').order_by('assigned_sequence_index')
        units = []
        for index, assignment in enumerate(assignments):
            question = assignment.snapshot.source_question
            answer = ESSAY if question.educational_type == 'essay' else question.correct_answer
            if question.question_type == 'multiple_choice':
                answer = sorted(answer) if isinstance(answer, list) else [key.strip() for key in answer.split(',')]
            elif question.question_type == 'true_false':
                answer = canonical_choice_response_state(question.question_type, answer)
            else:
                answer = str(answer)
            item = {'assignment_id': assignment.pk, 'question_id': question.pk, 'question_type': question.question_type,
                'educational_type': question.educational_type, 'answer': answer, 'confidence': 40 + index}
            group = question.group
            unit_key = f'group:{group.pk}' if group else f'question:{question.pk}'
            if not units or units[-1]['key'] != unit_key:
                units.append({'key': unit_key, 'kind': group.kind if group else question.educational_type if question.educational_type != 'standard' else question.question_type,
                    'title': group.title if group else question.title, 'items': []})
            units[-1]['items'].append(item)
        if len(units) != 12 or sum(len(unit['items']) for unit in units) != 22:
            raise CommandError('Site v2 fixture must publish exactly 12 complete questions and 22 independent response items.')
        return {'teacher': {'username': teacher.username, 'password': 'SiteV2Teacher123!'},
            'student': {'username': student.username, 'password': 'SiteV2Student123!'},
            'course_id': course.pk, 'task_id': task.pk, 'experiment_id': experiment.pk, 'units': units}

    @staticmethod
    def _user(username, password, role):
        user, _ = User.objects.get_or_create(username=username, defaults={'role': role, 'display_name': username})
        user.role = role
        user.is_active = True
        user.must_change_password = False
        user.uses_initial_password = False
        user.initial_password_state_recorded_at = timezone.now()
        if role == User.Role.STUDENT:
            user.student_number = 'SITE-V2-TYPES-001'
        user.set_password(password)
        user.save()
        return user
