from django.test import TransactionTestCase

from apps.accounts.models import Course, User
from apps.analytics.models import ExportJob
from apps.analytics.services import (
    ExportContractError,
    create_export_snapshot,
    run_export_job,
)
from apps.experiments.models import Experiment, ExperimentVersion
from apps.question_bank.models import QuestionBank


class ExportEdgeAuditTests(TransactionTestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="export-edge-audit-teacher",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="导出边界课程",
            code="EXPORT-EDGE",
            term="2026 秋",
        )
        bank = QuestionBank.objects.create(
            owner=self.teacher,
            course=self.course,
            name="导出边界题库",
        )
        self.experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=bank,
            name="导出边界实验",
        )
        self.version = ExperimentVersion.objects.create(
            experiment=self.experiment,
            version_number=1,
            published_by=self.teacher,
        )

    def _snapshot(self):
        return create_export_snapshot(
            course=self.course,
            experiment=self.experiment,
            created_by=self.teacher,
        )

    def test_kt_snapshot_requires_one_captured_version_without_a_scope(self):
        ExperimentVersion.objects.create(
            experiment=self.experiment,
            version_number=2,
            published_by=self.teacher,
        )

        with self.assertRaisesRegex(ExportContractError, "单一实验版本"):
            create_export_snapshot(
                course=self.course,
                experiment=self.experiment,
                created_by=self.teacher,
                package="kt",
            )

    def test_specialized_job_rejects_a_multi_version_frozen_snapshot(self):
        ExperimentVersion.objects.create(
            experiment=self.experiment,
            version_number=2,
            published_by=self.teacher,
        )
        snapshot = self._snapshot()

        for package in ("kt", "cd", "teaching"):
            with self.subTest(package=package):
                job = ExportJob.objects.create(
                    course=self.course,
                    experiment=self.experiment,
                    snapshot=snapshot,
                    created_by=self.teacher,
                    package=package,
                )

                with self.assertRaisesRegex(ExportContractError, "单一实验版本"):
                    run_export_job(job)

    def test_teaching_package_rejects_a_table_selection_it_cannot_materialize(self):
        job = ExportJob.objects.create(
            course=self.course,
            experiment=self.experiment,
            snapshot=self._snapshot(),
            created_by=self.teacher,
            package="teaching",
            selected_tables=["sessions"],
        )

        with self.assertRaisesRegex(ExportContractError, "教学包"):
            run_export_job(job)

    def test_fixed_schema_package_rejects_unimplemented_field_selection(self):
        job = ExportJob.objects.create(
            course=self.course,
            experiment=self.experiment,
            snapshot=self._snapshot(),
            created_by=self.teacher,
            package="complete",
            selected_fields=["final_answer"],
        )

        with self.assertRaisesRegex(ExportContractError, "字段级选择"):
            run_export_job(job)
