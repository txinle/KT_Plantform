from unittest import skipUnless

from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from .teaching.contracts import MetricValue, StudentComparison
from .teaching.scope import analytics_read_transaction


@skipUnless(connection.vendor == "postgresql", "PostgreSQL transaction contract")
class PostgreSQLAnalyticsReadTransactionTests(TransactionTestCase):
    def test_read_transaction_is_repeatable_read_and_read_only(self):
        with analytics_read_transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT current_setting('transaction_isolation'), "
                    "current_setting('transaction_read_only')"
                )
                isolation, read_only = cursor.fetchone()

        self.assertEqual(isolation, "repeatable read")
        self.assertEqual(read_only, "on")


class TeachingDTOContractTests(TransactionTestCase):
    def test_student_comparison_keeps_observation_denominator_separate_from_student_sample(self):
        generated_at = timezone.now()
        student_value = MetricValue.fraction(
            generated_at=generated_at,
            numerator=3,
            denominator=4,
        )
        class_average = MetricValue.fraction(
            generated_at=generated_at,
            numerator=7,
            denominator=10,
            sample_size=12,
        )

        comparison = StudentComparison(
            generated_at=generated_at,
            metric_key="correct_rate",
            student_value=student_value,
            class_average=class_average,
            sample_size=12,
        )

        self.assertEqual(comparison.student_value.denominator, 4)
        self.assertEqual(comparison.class_average.denominator, 10)
        self.assertEqual(comparison.sample_size, 12)
