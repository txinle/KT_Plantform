from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounts.models import Course, User
from apps.question_bank.models import Concept, QuestionBank

from .models import Experiment, ExperimentSamplingKC


class SamplingRuleBoundaryTests(TestCase):
    def test_sampling_kc_must_be_active_and_belong_to_the_experiment_course(self):
        teacher = User.objects.create_user(
            username="sampling-boundary", role=User.Role.TEACHER
        )
        course = Course.objects.create(
            owner=teacher, name="课程", code="SAMPLE-BOUND", term="2026 秋"
        )
        other = Course.objects.create(
            owner=teacher, name="其他", code="SAMPLE-OTHER", term="2026 秋"
        )
        experiment = Experiment.objects.create(
            course=course,
            owner=teacher,
            created_by=teacher,
            question_bank=QuestionBank.objects.create(course=course, name="题库"),
            name="实验",
            sampling_mode=Experiment.SamplingMode.TOTAL_COUNT,
            sampling_total_count=1,
        )
        foreign = Concept.objects.create(course=other, code="KC1", name="外部 KC")
        archived = Concept.objects.create(
            course=course, code="KC1", name="归档 KC", status=Concept.Status.ARCHIVED
        )

        with self.assertRaises(ValidationError):
            ExperimentSamplingKC.objects.create(
                experiment=experiment, concept=foreign
            )
        with self.assertRaises(ValidationError):
            ExperimentSamplingKC.objects.create(
                experiment=experiment, concept=archived
            )
