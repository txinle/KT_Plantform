import json
import os

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.accounts.research_consent import CURRENT_RESEARCH_CONSENT_VERSION, accept_current_research_consent
from apps.experiments.models import (
    ClassExperimentTask, Experiment, ExperimentClassRandomizationIdentity, ExperimentQuestion,
    ExperimentVersion, ExperimentVersionParticipantSnapshot, QuestionSnapshot,
)
from apps.experiments.services import assign_students
from apps.question_bank.models import Concept, CourseQuestionIdentity, QuestionBank


COURSE_CODE = 'R3-ENGLISH-E2E'
COURSE_TERM = 'R3-2026'
STANDALONE_BANK = 'R3 E2E standalone empty bank'
STANDALONE_EXPERIMENT = 'R3 E2E standalone English groups'


class Command(BaseCommand):
    help = 'Reset only the isolated R3 English group fixture in kt_e2e; leave both banks empty for real UI import.'

    def handle(self, *args, **options):
        if os.environ.get('POSTGRES_DB') != 'kt_e2e' or str(connection.settings_dict['NAME']) != 'kt_e2e':
            raise CommandError('seed_e2e_english_groups only runs against kt_e2e with POSTGRES_DB=kt_e2e.')
        payload = self._seed()
        self.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    @transaction.atomic
    def _seed(self):
        teacher = self._user('r3-english-teacher', 'R3Teacher123!', User.Role.TEACHER)
        student = self._user('r3-english-student', 'R3Student123!', User.Role.STUDENT)
        course, _ = Course.objects.get_or_create(owner=teacher, code=COURSE_CODE, term=COURSE_TERM, defaults={'name': 'R3 English E2E'})
        course.status = Course.Status.ACTIVE
        course.save(update_fields=['status'])
        self._reset_owned(course, teacher)
        class_group, _ = ClassGroup.objects.get_or_create(course=course, name='R3 English E2E class', academic_year='2026', defaults={'owner': teacher})
        student.class_group = class_group
        student.save(update_fields=['class_group'])
        CourseEnrollment.objects.update_or_create(course=course, student=student, defaults={'class_group': class_group, 'is_active': True})
        accept_current_research_consent(student=student, version=CURRENT_RESEARCH_CONSENT_VERSION)
        leaf, _ = Concept.objects.get_or_create(course=course, code='KC1', defaults={'name': 'English comprehension'})
        private, _ = Concept.objects.get_or_create(owner=teacher, scope=Concept.Scope.PRIVATE, normalized_name='r3 english vocabulary', defaults={'name': 'R3 English vocabulary'})
        course_bank = QuestionBank.objects.create(course=course, owner=teacher, name='R3 E2E course empty bank')
        standalone_bank = QuestionBank.objects.create(owner=teacher, name=STANDALONE_BANK)
        experiment = Experiment.objects.create(course=course, owner=teacher, created_by=teacher, question_bank=course_bank,
            name='R3 E2E course English groups', selection_mode=Experiment.SelectionMode.ALL, sampling_mode=None, ordering_mode=Experiment.Ordering.FIXED,
            feedback_mode=Experiment.Feedback.FULL, feedback_release_policy=Experiment.FeedbackReleasePolicy.AFTER_PERSONAL_SUBMISSION)
        assign_students(experiment, [student])
        return {'teacher': {'username': teacher.username, 'password': 'R3Teacher123!'},
            'student': {'username': student.username, 'password': 'R3Student123!'},
            'course_id': course.pk, 'class_id': class_group.pk, 'student_id': student.pk, 'kc_code': leaf.code,
            'private_kc_id': private.pk, 'course_bank_id': course_bank.pk, 'standalone_bank_id': standalone_bank.pk,
            'course_experiment_id': experiment.pk}

    @staticmethod
    def _user(username, password, role):
        user, _ = User.objects.get_or_create(username=username, defaults={'role': role, 'display_name': username})
        user.role = role
        user.is_active = True
        user.must_change_password = False
        user.uses_initial_password = False
        user.initial_password_state_recorded_at = timezone.now()
        if role == User.Role.STUDENT:
            user.student_number = 'R3-EN-001'
        user.set_password(password)
        user.save()
        return user

    @staticmethod
    def _reset_owned(course, teacher):
        from apps.accounts.management.commands.seed_demo import _delete_demo_dependency_closure
        from apps.assessment.models import Attempt, ExperimentSession
        experiments = Experiment.objects.filter(Q(course=course) | Q(course__isnull=True, owner=teacher, name=STANDALONE_EXPERIMENT))
        experiment_ids = list(experiments.values_list('pk', flat=True))
        banks = QuestionBank.objects.filter(Q(course=course) | Q(course__isnull=True, owner=teacher, name=STANDALONE_BANK))
        bank_ids = list(banks.values_list('pk', flat=True))
        if Experiment.objects.filter(Q(question_bank_id__in=bank_ids) | Q(question_bank_links__question_bank_id__in=bank_ids)).exclude(pk__in=experiment_ids).exists() or ExperimentQuestion.objects.filter(question__bank_id__in=bank_ids).exclude(experiment_id__in=experiment_ids).exists() or QuestionSnapshot.objects.filter(source_question__bank_id__in=bank_ids).exclude(version__experiment_id__in=experiment_ids).exists():
            raise CommandError('R3 fixture banks are referenced outside the owned fixture; reset refused.')
        # Break capture-v2 PROTECT cycles only inside this disposable fixture.
        Attempt.objects.filter(session__version__experiment_id__in=experiment_ids).update(
            active_response_event=None, final_response_event=None)
        for experiment in list(experiments):
            _delete_demo_dependency_closure(ExperimentSession.objects.filter(version__experiment=experiment))
            _delete_demo_dependency_closure(ExperimentVersionParticipantSnapshot.objects.filter(version__experiment=experiment))
            _delete_demo_dependency_closure(ClassExperimentTask.objects.filter(experiment=experiment))
            _delete_demo_dependency_closure(experiment.publish_plans.all())
            _delete_demo_dependency_closure(ExperimentClassRandomizationIdentity.objects.filter(experiment=experiment))
            models.QuerySet.update(Experiment.objects.filter(pk=experiment.pk), frozen_release_snapshot=None, status=Experiment.Status.DRAFT)
            _delete_demo_dependency_closure(ExperimentVersion.objects.filter(experiment=experiment))
            _delete_demo_dependency_closure(Experiment.objects.filter(pk=experiment.pk))
        _delete_demo_dependency_closure(banks)
        CourseQuestionIdentity.objects.filter(course=course, question__isnull=True).delete()
