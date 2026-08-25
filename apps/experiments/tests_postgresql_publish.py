from django.test import TestCase, skipUnlessDBFeature

from apps.accounts.models import User
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept

from .models import Experiment, ExperimentQuestion, ExperimentStudent, ExperimentVersion
from .services import publish_experiment


@skipUnlessDBFeature("has_select_for_update_of")
class PostgreSQLPublishLockRegressionTests(TestCase):
    """Exercise the nullable course-identity joins under real row locking."""

    def test_publish_locks_only_base_rows_when_course_identity_is_nullable(self):
        teacher = User.objects.create_user(
            username="postgres-publish-teacher",
            role=User.Role.TEACHER,
        )
        student = User.objects.create_user(
            username="POSTGRES-PUBLISH-STUDENT",
            student_number="POSTGRES-PUBLISH-STUDENT",
            role=User.Role.STUDENT,
        )
        bank = QuestionBank.objects.create(owner=teacher, name="PostgreSQL 发布题库")
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="PostgreSQL 行锁是否兼容？",
            correct_answer="是",
        )
        concept = Concept.objects.create(
            owner=teacher,
            scope=Concept.Scope.PRIVATE,
            name="发布行锁",
            normalized_name="发布行锁",
        )
        QuestionConcept.objects.create(
            question=question,
            concept=concept,
            is_primary=True,
        )
        experiment = Experiment.objects.create(
            owner=teacher,
            question_bank=bank,
            name="PostgreSQL 发布回归",
        )
        ExperimentQuestion.objects.create(
            experiment=experiment,
            question=question,
            display_order=1,
        )
        ExperimentStudent.objects.create(experiment=experiment, student=student)

        version = publish_experiment(experiment, teacher)

        self.assertEqual(version.version_number, 1)
        self.assertTrue(ExperimentVersion.objects.filter(pk=version.pk).exists())
        experiment.refresh_from_db()
        self.assertEqual(experiment.status, Experiment.Status.PUBLISHED)
