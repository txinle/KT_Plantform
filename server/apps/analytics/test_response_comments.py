from uuid import uuid4

from django.test import TestCase
from django.utils import timezone

from apps.assessment.models import GradeRecord, SubmissionEvent
from . import test_teacher_data_center_api as fixtures
from .data_scope import DataScope
from .research_contract import FILES
from .research_dataset import read_research_bundle


class ResponseCommentTests(TestCase):
    setUp = fixtures.TeacherDataCenterApiTests.setUp

    def response(self):
        scope = DataScope.from_mapping(self.course, {
            'experiment_id': self.experiment.pk, 'class_id': self.class_group.pk})
        bundle, _ = read_research_bundle(scope, include_course_catalog=False)
        headers = next(item['headers'] for item in FILES if item['key'] == 'responses')
        self.assertEqual(headers[-2:], ['是否正确', 'comment'])
        rows = bundle['files']['responses']
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), len(headers))
        return dict(zip(headers, rows[0], strict=True))

    def grade(self, *, comment='', method='manual_binary_v1', completed=True):
        submission = SubmissionEvent.objects.create(attempt=self.attempt, submission_token=uuid4(),
            final_answer='student answer', committed_at=timezone.now(), server_received_at=timezone.now(),
            grading_status='completed' if completed else 'pending')
        return GradeRecord.objects.create(submission=submission, version=1,
            status='completed' if completed else 'pending', validity_status='effective' if completed else 'candidate',
            score=1, max_score=1, is_correct=True, grading_method=method,
            grader=self.teacher if method == 'manual_binary_v1' else None, comment=comment)

    def test_teacher_reason_is_exported_verbatim_in_the_same_response_row(self):
        self.grade(comment='推理完整，但缺少单位。\n请补充单位。')
        self.assertEqual(self.response()['comment'], '推理完整，但缺少单位。\n请补充单位。')

    def test_ungraded_response_has_empty_comment(self):
        self.assertEqual(self.response()['comment'], '')

    def test_automatic_grading_does_not_export_internal_explanation(self):
        self.grade(comment='internal automatic rationale', method='automatic')
        self.assertEqual(self.response()['comment'], '')

    def test_manual_grade_without_opinion_remains_empty(self):
        self.grade()
        self.assertEqual(self.response()['comment'], '')

    def test_pending_grade_does_not_export_a_reason(self):
        self.grade(comment='not a completed judgement', completed=False)
        self.assertEqual(self.response()['comment'], '')

    def test_superseded_teacher_reason_is_not_exported(self):
        previous = self.grade(comment='旧判定理由')
        GradeRecord.objects.filter(pk=previous.pk).update(validity_status='superseded')
        GradeRecord.objects.create(submission=previous.submission, version=2, status='completed',
            validity_status='effective', score=0, max_score=1, is_correct=False,
            grading_method='manual_binary_v1', grader=self.teacher, comment='复核后的理由', supersedes_grade=previous)
        self.assertEqual(self.response()['comment'], '复核后的理由')


class ResponseCommentExportTests(TestCase):
    def setUp(self):
        from .test_research_export_v2 import ResearchExportJobV2Tests
        ResearchExportJobV2Tests.setUp(self)

    def export_rows(self, *, historical=False):
        import csv
        import io
        import zipfile
        from .test_research_export_v2 import ResearchExportJobV2Tests
        from .models import ExportJob
        from .services import claim_export_job, run_export_job
        headers = next(item['headers'] for item in FILES if item['key'] == 'responses')
        row = [''] * len(headers)
        row[0] = f'S{self.attempt.session_id}'
        row[3] = 'Q001'
        row[-2:] = ['是', '保留原始批改理由\n第二行']
        if historical:
            row = row[:-1]
        self.materialization.response_rows.update(row=row)
        response = self.client.post(f'/api/v1/teacher/courses/{self.course.pk}/data/export-jobs/',
            ResearchExportJobV2Tests.payload(self), content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        job = ExportJob.objects.select_related('snapshot').get()
        if historical:
            job.snapshot.source_bundle['research_export_v2'].pop('response_headers')
            from .research_export_v2 import _json_hash
            type(job.snapshot).objects.filter(pk=job.snapshot.pk).update(
                source_bundle=job.snapshot.source_bundle, source_bundle_hash=_json_hash(job.snapshot.source_bundle))
        completed = run_export_job(claim_export_job(worker_id='comment-zip'))
        self.assertEqual(completed.status, 'succeeded')
        with zipfile.ZipFile(completed.artifact_path) as archive:
            self.assertEqual(len(archive.namelist()), 8)
            return list(csv.reader(io.StringIO(archive.read('05_单次实验_逐题作答表.csv').decode('utf-8-sig'))))

    def test_new_zip_contains_comment_after_correctness_without_an_extra_row(self):
        rows = self.export_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][-2:], ['是否正确', 'comment'])
        self.assertEqual(rows[1][-2:], ['是', '保留原始批改理由\n第二行'])
        self.assertEqual(len(rows[0]), len(rows[1]))

    def test_historical_frozen_zip_keeps_its_original_response_header(self):
        rows = self.export_rows(historical=True)
        self.assertNotIn('comment', rows[0])
        self.assertEqual(rows[0][-1], '是否正确')
        self.assertEqual(len(rows[0]), len(rows[1]))
