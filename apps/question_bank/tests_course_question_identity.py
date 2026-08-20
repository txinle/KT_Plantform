from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.core.management import call_command
from django.db import IntegrityError
from unittest.mock import patch

from apps.accounts.models import Course, User

from . import models
from .models import CourseQuestionIdentity, Question, QuestionBank
from .services.course_question_identity import allocate_course_question_identity
from .services.import_parsing import ParsedQuestion
from .services.import_service import copy_shared_bank, import_questions


class CourseQuestionIdentityModelContractTests(SimpleTestCase):
    def test_course_question_identity_is_a_question_bank_model(self):
        self.assertTrue(hasattr(models, "CourseQuestionIdentity"))


class CourseQuestionIdentityAllocatorTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="identity-teacher",
            role=User.Role.TEACHER,
        )
        self.course_a = Course.objects.create(
            owner=self.teacher,
            name="课程 A",
            code="A101",
            term="2026 秋",
        )
        self.course_b = Course.objects.create(
            owner=self.teacher,
            name="课程 B",
            code="B101",
            term="2026 秋",
        )
        self.bank_a = QuestionBank.objects.create(
            course=self.course_a,
            created_by=self.teacher,
            name="课程 A 题库",
        )
        self.bank_b = QuestionBank.objects.create(
            course=self.course_b,
            created_by=self.teacher,
            name="课程 B 题库",
        )

    @staticmethod
    def _question(bank, sequence):
        return Question.objects.create(
            bank=bank,
            sequence=sequence,
            question_type=Question.Type.SHORT,
            text=f"题目 {sequence}",
            correct_answer="答案",
        )

    @staticmethod
    def _bulk_question(bank, sequence):
        question = Question(
            bank=bank,
            sequence=sequence,
            question_type=Question.Type.SHORT,
            text=f"批量题目 {sequence}",
            correct_answer="答案",
        )
        Question.objects.bulk_create([question])
        question.refresh_from_db()
        return question

    def test_allocator_assigns_independent_course_sequences_and_stable_codes(self):
        question_a = self._bulk_question(self.bank_a, 50)
        question_b = self._bulk_question(self.bank_b, 1)

        identity_a = allocate_course_question_identity(self.course_a, question_a)
        identity_b = allocate_course_question_identity(self.course_b, question_b)

        self.assertEqual(identity_a.course_sequence, 1)
        self.assertEqual(identity_b.course_sequence, 1)
        self.assertEqual(identity_a.stable_code, f"C{self.course_a.pk:06d}-Q000001")
        self.assertEqual(identity_b.stable_code, f"C{self.course_b.pk:06d}-Q000001")

    def test_direct_orm_create_assigns_identity_for_a_course_question(self):
        question = self._question(self.bank_a, 1)

        self.assertTrue(
            CourseQuestionIdentity.objects.filter(
                question=question,
                course=self.course_a,
                course_sequence=1,
                stable_code=f"C{self.course_a.pk:06d}-Q000001",
            ).exists()
        )

    def test_moving_an_identified_question_to_another_course_bank_is_rejected(self):
        question = self._question(self.bank_a, 1)
        identity = allocate_course_question_identity(self.course_a, question)
        question.bank = self.bank_b

        with self.assertRaises(ValidationError):
            question.save(update_fields=["bank"])

        question.refresh_from_db()
        identity.refresh_from_db()
        self.assertEqual(question.bank_id, self.bank_a.pk)
        self.assertEqual(identity.course_id, self.course_a.pk)

    def test_moving_within_the_same_course_keeps_the_existing_identity(self):
        second_course_bank = QuestionBank.objects.create(
            course=self.course_a,
            created_by=self.teacher,
            name="课程 A 的第二题库",
        )
        question = self._question(self.bank_a, 1)
        identity = CourseQuestionIdentity.objects.get(question=question)
        question.bank = second_course_bank
        question.save(update_fields=["bank"])

        identity.refresh_from_db()
        self.assertEqual(question.bank_id, second_course_bank.pk)
        self.assertEqual(identity.question_id, question.pk)
        self.assertEqual(identity.course_id, self.course_a.pk)

    def test_legacy_question_creation_remains_without_a_course_identity(self):
        legacy_bank = QuestionBank.objects.create(
            created_by=self.teacher,
            name="历史兼容题库",
        )

        question = self._question(legacy_bank, 1)

        self.assertFalse(CourseQuestionIdentity.objects.filter(question=question).exists())

    def test_import_assigns_one_identity_to_each_question_in_the_atomic_batch(self):
        parsed_rows = [
            ParsedQuestion(10, "导入题目一", {}, "答案一", Question.Type.SHORT),
            ParsedQuestion(20, "导入题目二", {}, "答案二", Question.Type.SHORT),
        ]

        questions = import_questions(self.bank_a, parsed_rows)

        identities = list(
            CourseQuestionIdentity.objects.filter(course=self.course_a).order_by("course_sequence")
        )
        self.assertEqual([identity.question_id for identity in identities], [question.pk for question in questions])
        self.assertEqual([identity.course_sequence for identity in identities], [1, 2])

    def test_copy_assigns_fresh_course_identity_to_each_copied_question(self):
        existing_question = self._question(self.bank_a, 1)
        allocate_course_question_identity(self.course_a, existing_question)
        source_bank = QuestionBank.objects.create(
            course=self.course_b,
            created_by=self.teacher,
            name="共享源题库",
            visibility=QuestionBank.Visibility.SHARED,
        )
        source_question = self._question(source_bank, 7)

        copied_bank = copy_shared_bank(source_bank, self.course_a, self.teacher)

        copied_question = copied_bank.questions.get(sequence=source_question.sequence)
        copied_identity = CourseQuestionIdentity.objects.get(question=copied_question)
        self.assertEqual(copied_identity.course, self.course_a)
        self.assertEqual(copied_identity.course_sequence, 2)
        self.assertEqual(copied_identity.stable_code, f"C{self.course_a.pk:06d}-Q000002")

    def test_demo_seed_assigns_identities_to_its_new_course_questions(self):
        call_command("seed_demo")

        course = Course.objects.get(code="KT101", term="2026春")
        self.assertEqual(
            CourseQuestionIdentity.objects.filter(course=course, question__isnull=False).count(),
            course.question_banks.get(name="演示题库").questions.count(),
        )

    def test_question_deletion_keeps_the_identity_and_the_next_number_is_not_reused(self):
        first_question = self._question(self.bank_a, 1)
        first_identity = allocate_course_question_identity(self.course_a, first_question)

        first_question.delete()
        first_identity.refresh_from_db()
        second_question = self._question(self.bank_a, 2)
        second_identity = allocate_course_question_identity(self.course_a, second_question)

        self.assertIsNone(first_identity.question)
        self.assertEqual(first_identity.course_sequence, 1)
        self.assertEqual(second_identity.course_sequence, 2)
        self.assertEqual(second_identity.stable_code, f"C{self.course_a.pk:06d}-Q000002")

    def test_batch_import_rolls_back_questions_and_identities_together(self):
        duplicate_rows = [
            ParsedQuestion(1, "第一题", {}, "答案", Question.Type.SHORT),
            ParsedQuestion(1, "重复题", {}, "答案", Question.Type.SHORT),
        ]

        with self.assertRaises(IntegrityError):
            import_questions(self.bank_a, duplicate_rows)

        self.assertFalse(self.bank_a.questions.exists())
        self.assertFalse(CourseQuestionIdentity.objects.filter(course=self.course_a).exists())

    def test_database_uniqueness_prevents_two_course_identities_from_using_one_sequence(self):
        first_question = self._question(self.bank_a, 1)
        second_question = self._bulk_question(self.bank_a, 2)
        first_identity = allocate_course_question_identity(self.course_a, first_question)

        with self.assertRaises(IntegrityError):
            CourseQuestionIdentity.objects.create(
                course=self.course_a,
                question=second_question,
                course_sequence=first_identity.course_sequence,
                stable_code=f"C{self.course_a.pk:06d}-Q999999",
            )

    def test_database_rejects_a_nonpositive_course_sequence(self):
        question = self._bulk_question(self.bank_a, 1)

        with self.assertRaises(IntegrityError):
            CourseQuestionIdentity.objects.create(
                course=self.course_a,
                question=question,
                course_sequence=0,
                stable_code=f"C{self.course_a.pk:06d}-Q000000",
            )

    def test_allocator_retries_a_stale_unique_collision_for_sqlite_compatible_concurrency_coverage(self):
        question = self._bulk_question(self.bank_a, 1)
        create = CourseQuestionIdentity.objects.create
        attempts = 0

        def stale_once(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise IntegrityError("simulated stale course sequence")
            return create(*args, **kwargs)

        with patch(
            "apps.question_bank.services.course_question_identity.CourseQuestionIdentity.objects.create",
            side_effect=stale_once,
        ):
            identity = allocate_course_question_identity(self.course_a, question)

        self.assertEqual(attempts, 2)
        self.assertEqual(identity.course_sequence, 1)
