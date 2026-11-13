import csv
import io
import json
import uuid
import zipfile
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from openpyxl import load_workbook

from apps.api.test_support import domain_payload
from apps.assessment.models import Attempt, GradeRecord, StudentQuestionAssignment, SubmissionEvent
from apps.experiments.models import QuestionGroupSnapshot, QuestionSnapshot
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.question_bank.models import Question, QuestionGroup
from .models import ResearchSessionResponse
from . import test_research_export_v2 as export_fixtures


class QuestionGroupExportTests(TestCase):
    def setUp(self):
        export_fixtures.ResearchExportJobV2Tests.setUp(self)

    def scope(self):
        from .export_scope import ExperimentExportScope
        return ExperimentExportScope.from_mapping(self.course, {'scope_version': 2, 'experiment_id': self.experiment.pk, 'class_mode': 'all'})

    def freeze(self):
        from .research_export_v2 import create_research_export_snapshot
        return create_research_export_snapshot(course=self.course, experiment=self.experiment, created_by=self.teacher, scope=self.scope())

    def add_grouped_response(self):
        bank = self.experiment.question_bank
        group = QuestionGroup.objects.create(bank=bank, kind='cloze', title='Frozen story', material='At {{b01}}.')
        source = Question.objects.create(bank=bank, group=group, group_order=1, blank_id='b01', sequence=2,
            question_type='fill_blank', text='', correct_answer='home', max_score='2.50', text_match_case_sensitive=True)
        frozen = QuestionGroupSnapshot.objects.create(version=self.version, source_group_id=group.pk,
            source_revision=1, kind='cloze', title=group.title, material=group.material, content_language='en')
        snapshot = QuestionSnapshot.objects.create(version=self.version, source_question=source, source_sequence=2,
            group_snapshot=frozen, group_order=1, blank_id='b01', max_score='2.50', text_match_case_sensitive=True,
            question_type='fill_blank', text='', correct_answer='home', code='GROUP-Q2')
        assignment = StudentQuestionAssignment.objects.create(session=self.session, snapshot=snapshot,
            item_instance=get_or_create_default_item_instance(snapshot), assigned_sequence_index=2)
        attempt = Attempt.objects.create(session=self.session, assignment=assignment)
        submission = SubmissionEvent.objects.create(attempt=attempt, submission_token=uuid.uuid4(), final_answer='home',
            server_received_at=timezone.now(), committed_at=timezone.now())
        grade = GradeRecord.objects.create(submission=submission, status='completed', validity_status='effective',
            is_correct=True, score=Decimal('2.50'), max_score=Decimal('2.50'))
        self.session.refresh_from_db()
        self.materialization.source_revision = self.session.research_source_revision
        self.materialization.row_counts = {'responses': 2, 'behavior': 1}
        self.materialization.save(update_fields=['source_revision', 'row_counts'])
        ResearchSessionResponse.objects.create(materialization=self.materialization, question_ref='Q002', assigned_order=2, ordinal=2,
            row=[f'S{self.session.pk}', '', '', 'Q002', 2, '', '', 1, '', '', '', '', 'home', '已作答', True])
        return group, frozen, snapshot, grade

    def test_grouped_export_reuses_standard_v2_rows_and_fixed_question_library(self):
        from .services import create_export_job, claim_export_job, run_export_job
        from .research_contract import FILES
        group, _frozen, _question, grade = self.add_grouped_response()
        snapshot = self.freeze()
        self.assertEqual(snapshot.source_bundle['version'], 2)
        create_export_job(course=self.course, experiment=self.experiment, created_by=self.teacher, snapshot=snapshot, package='experiment')
        group.material = 'Changed after freeze'
        group.save(update_fields=['material'])
        GradeRecord.objects.filter(pk=grade.pk).update(score=0)
        with patch('apps.analytics.research_export_v2._course_asset_files', side_effect=AssertionError('live course read')):
            ready = run_export_job(claim_export_job(worker_id='group-standard'))
        self.assertEqual(ready.status, 'succeeded')
        with zipfile.ZipFile(ready.artifact_path) as archive:
            self.assertEqual(set(archive.namelist()), {item['path'] for item in FILES})
            rows = list(csv.DictReader(io.StringIO(archive.read('05_单次实验_逐题作答表.csv').decode('utf-8-sig'))))
            self.assertEqual(len(rows), 2)
            self.assertEqual(list(rows[0]), next(item['headers'] for item in FILES if item['key'] == 'responses'))
            library = json.loads(archive.read('课程题目固定库.json'))
            member = next(item for item in library if item.get('题组'))
            self.assertEqual(member['题组']['材料'], 'At {{b01}}.')
            self.assertEqual(member['题组']['组内顺序'], 1)
            self.assertEqual(member['题组']['空位编号'], 'b01')
            self.assertEqual(member['题组']['分值'], '2.50')

    def test_frozen_v2_materialization_remains_eight_files_after_grouped_release_added(self):
        from .services import create_export_job, claim_export_job, run_export_job
        old = self.freeze()
        self.assertEqual(old.source_bundle['version'], 2)
        self.add_grouped_response()
        create_export_job(course=self.course, experiment=self.experiment, created_by=self.teacher, snapshot=old, package='experiment')
        ready = run_export_job(claim_export_job(worker_id='old-v2'))
        self.assertEqual(ready.status, 'succeeded')
        with zipfile.ZipFile(ready.artifact_path) as archive:
            self.assertEqual(len(archive.namelist()), 8)
            self.assertNotIn('08_题组与小题冻结表.csv', archive.namelist())
            header = next(csv.reader(io.StringIO(archive.read('05_单次实验_逐题作答表.csv').decode('utf-8-sig'))))
            self.assertNotIn('小题满分', header)

    def test_grouped_catalog_retains_standard_eight_files_without_extra_columns(self):
        from .research_contract import FILES
        self.add_grouped_response()
        response = self.client.get(f'/api/v1/teacher/courses/{self.course.pk}/data/exports/', self.scope().serialize())
        self.assertEqual(response.status_code, 200, response.content)
        payload = domain_payload(response)
        self.assertNotIn('contract_version', payload)
        self.assertEqual([row['path'] for row in payload['tables']], [item['path'] for item in FILES])
        self.assertTrue(all('columns' not in row for row in payload['tables']))

    def test_weighted_ordinary_release_keeps_the_standard_export_contract(self):
        bank = self.experiment.question_bank
        source = Question.objects.create(bank=bank, sequence=2, text='City?', question_type='fill_blank', correct_answer='Paris', max_score='2.50')
        QuestionSnapshot.objects.create(version=self.version, source_question=source, source_sequence=2, code='WEIGHTED',
            text='City?', question_type='fill_blank', correct_answer='Paris', max_score='2.50')
        snapshot = self.freeze()
        self.assertEqual(snapshot.source_bundle['version'], 2)

    def test_existing_identified_export_keeps_one_row_per_question_and_canonical_score(self):
        _group, _frozen, question, grade = self.add_grouped_response()
        Attempt.objects.filter(pk=grade.submission.attempt_id).update(score=Decimal('99.00'), final_answer='stale')
        response = self.client.post(f'/api/v1/teacher/experiments/{self.experiment.pk}/identified.xlsx',
            {'fields': ['session_id', 'final_answer', 'is_correct', 'score']})
        self.assertEqual(response.status_code, 200, response.content)
        values = list(load_workbook(io.BytesIO(response.content), read_only=True, data_only=True).active.values)
        rows = [dict(zip(values[0], row)) for row in values[1:]]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row['会话 ID'] for row in rows}, {self.session.pk})
        self.assertEqual(len({row['题库唯一编号'] for row in rows}), 2)
        member = next(row for row in rows if row['题库唯一编号'] == question.code)
        self.assertEqual(member['得分'], 2.5)
        self.assertEqual(member['最终答案'], 'home')
        self.assertEqual(values[0], ('学号', '姓名', '题库唯一编号', '会话 ID', '最终答案', '是否正确', '得分'))

    def test_latest_content_fields_remain_in_the_existing_fixed_library(self):
        from .research_export_v2 import _course_asset_files
        group, _, _, _ = self.add_grouped_response()
        group.kind = 'words'
        group.word_bank = ['home', 'school']
        group.save(update_fields=['kind', 'word_bank'])
        Question.objects.filter(pk=self.experiment.question_bank.questions.filter(group__isnull=True).first().pk).update(educational_type='translation',
            title='English to Chinese', instructions='Translate the text.')
        files, _ = _course_asset_files(self.course)
        library = json.loads(files['课程题目固定库.json'])
        grouped = next(row for row in library if row.get('题组'))
        self.assertEqual(grouped['题组']['词库'], ['home', 'school'])
        writing = next(row for row in library if row.get('教学题型'))
        self.assertEqual(writing['教学题型'], 'translation')
        self.assertEqual(writing['标题'], 'English to Chinese')
        self.assertEqual(writing['作答说明'], 'Translate the text.')
