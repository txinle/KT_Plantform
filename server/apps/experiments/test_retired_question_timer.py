from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from apps.accounts.models import Course, User
from apps.question_bank.models import QuestionBank
from apps.api.test_support import domain_payload
from .api.serializers import TeacherExperimentSerializer
from .models import ExperimentVersion
from .publication_services import append_schedule_revision, get_or_create_publish_plan

from .api.serializers import ScheduleRevisionInputSerializer
from .api.standalone_serializers import StandaloneExperimentInputSerializer
from .models import Experiment
from .services import validate_new_time_configuration


class RetiredQuestionTimerTests(SimpleTestCase):
    def test_standalone_api_rejects_new_question_timer(self):
        serializer = StandaloneExperimentInputSerializer(data={
            'time_mode': 'per_question', 'per_question_time_limit_seconds': 30,
        })
        self.assertFalse(serializer.is_valid())

    def test_schedule_api_rejects_question_timer_extension(self):
        serializer = ScheduleRevisionInputSerializer(data={
            'expected_revision': 1, 'per_question_time_limit_seconds': 90,
        })
        self.assertFalse(serializer.is_valid())

    def test_service_rejects_retired_mode_and_orphan_limit(self):
        for mode, seconds in [('per_question', 30), ('none', 30)]:
            with self.subTest(mode=mode), self.assertRaises(ValidationError):
                validate_new_time_configuration(mode, seconds)

    def test_service_allows_current_modes_without_changing_historical_enum(self):
        for mode in ['none', 'total']:
            validate_new_time_configuration(mode)
        self.assertEqual(Experiment.TimeMode.PER_QUESTION, 'per_question')


class RetiredQuestionTimerWriteTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username='retired-timer-teacher', role='teacher')
        self.course = Course.objects.create(name='Timer course', code='TIMER', owner=self.teacher)
        self.bank = QuestionBank.objects.create(name='Timer bank', course=self.course, owner=self.teacher)
        self.experiment = Experiment.objects.create(
            name='Historical draft', owner=self.teacher, course=self.course, question_bank=self.bank,
            sampling_mode='total_count', sampling_total_count=1,
            time_mode='per_question', per_question_time_limit_seconds=30,
        )

    def test_course_draft_requires_explicit_current_mode(self):
        unchanged = TeacherExperimentSerializer(self.experiment, data={'name': 'Renamed'}, partial=True, context={'course': self.course})
        self.assertFalse(unchanged.is_valid())
        self.assertIn('time_mode', unchanged.errors)
        changed = TeacherExperimentSerializer(self.experiment, data={'time_mode': 'none'}, partial=True, context={'course': self.course})
        self.assertTrue(changed.is_valid(), changed.errors)
        changed.save()
        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.time_mode, 'none')
        self.assertIsNone(self.experiment.per_question_time_limit_seconds)

    def test_frozen_question_timer_cannot_expand_publication(self):
        release = ExperimentVersion.objects.create(
            experiment=self.experiment, version_number=1, published_by=self.teacher,
            configuration={'time_mode': 'per_question', 'per_question_time_limit_seconds': 30},
        )
        self.experiment.frozen_release_snapshot = release
        self.experiment.status = 'published'
        self.experiment.save()
        with self.assertRaises(ValidationError):
            get_or_create_publish_plan(self.experiment, self.teacher)
        projection = TeacherExperimentSerializer(self.experiment, context={'can_manage': True}).data
        self.assertFalse(projection['can_publish'])
        self.assertEqual(projection['per_question_time_limit_seconds'], 30)

    def test_schedule_service_rejects_new_item_extension_before_touching_task(self):
        with self.assertRaises(ValidationError):
            append_schedule_revision(None, self.teacher, per_question_time_limit_seconds=60)

    def test_standalone_retired_draft_has_no_publish_action(self):
        bank = QuestionBank.objects.create(name='Standalone timer bank', owner=self.teacher)
        experiment = Experiment.objects.create(name='Old standalone', owner=self.teacher, question_bank=bank, time_mode='per_question', per_question_time_limit_seconds=30)
        self.client.force_login(self.teacher)
        response = self.client.get(f'/api/v1/teacher/standalone-experiments/{experiment.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(domain_payload(response)['actions']['can_publish'])
