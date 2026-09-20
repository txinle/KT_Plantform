import csv
import io
import json
import os
import tempfile
import zipfile
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from apps.accounts.models import CourseEnrollment, User
from apps.analytics import test_teacher_data_center_api as fixtures
from apps.analytics.data_scope import DataScope
from apps.analytics.services import create_export_job, create_export_snapshot, run_export_job
from apps.question_bank.models import Question


FILENAMES = [
    "01_课程学生表.csv", "02_学生Session实验绑定表.csv", "03_课程知识点表.csv",
    "04_题目KC映射表.csv", "05_单次实验_逐题作答表.csv", "06_单次实验_行为过程表.csv",
    "07_表6事件字典.csv", "课程题目固定库.json",
]


class ResearchStandardContractTests(SimpleTestCase):
    def test_exact_public_files_and_visit_field(self):
        from apps.analytics.research_contract import FILES

        self.assertEqual([item["path"] for item in FILES], FILENAMES)
        self.assertIn("首次访问顺序", FILES[4]["headers"])
        self.assertNotIn("首次作答顺序", FILES[4]["headers"])
        self.assertEqual(len(FILES[5]["headers"]), 12)


class ResearchStandardTests(TestCase):
    def setUp(self):
        fixtures.TeacherDataCenterApiTests.setUp(self)
        self.query = {"experiment_id": self.experiment.pk, "class_id": self.class_group.pk}
        self.other_student = User.objects.create_user(username="standard-unstarted", role="student", student_number="00002")
        CourseEnrollment.objects.create(course=self.course, class_group=self.class_group, student=self.other_student)
        self.unassigned = Question.objects.create(bank=self.experiment.question_bank, sequence=2,
            question_type="short_answer", text="课程全量未分配题", correct_answer="  答案\n", teacher_difficulty="0.250")

    def api(self, suffix):
        return f"/api/v1/teacher/courses/{self.course.pk}/data/{suffix}"

    def export_files(self, **filters):
        scope = DataScope.from_mapping(self.course, {**self.query, **filters})
        snapshot = create_export_snapshot(course=self.course, experiment=self.experiment, scope=scope,
            package="experiment", created_by=self.teacher)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"EXPORT_ROOT": directory}):
            job = create_export_job(snapshot=snapshot, course=self.course, experiment=self.experiment,
                package="experiment", created_by=self.teacher)
            run_export_job(job)
            job.refresh_from_db()
            with zipfile.ZipFile(job.artifact_path) as archive:
                return {name: archive.read(name) for name in archive.namelist()}

    def test_course_libraries_remain_whole_under_student_filter(self):
        all_files = self.export_files()
        filtered = self.export_files(student_id=self.other_student.pk)
        self.assertEqual(list(all_files), FILENAMES)
        for filename in FILENAMES[:4] + FILENAMES[6:]:
            self.assertEqual(all_files[filename], filtered[filename], filename)
        roster = list(csv.DictReader(io.StringIO(filtered[FILENAMES[0]].decode("utf-8-sig"))))
        self.assertEqual({row["学号"] for row in roster}, {"DATA-001", "00002"})
        library = json.loads(filtered[FILENAMES[7]])
        self.assertEqual(len(library), 2)
        self.assertEqual(set(library[1]), {"题号", "题干", "选项", "答案", "解析", "难度"})
        self.assertEqual(library[1]["难度"], 0.25)
        self.assertEqual(library[1]["答案"], "  答案\n")
        self.assertEqual(len(list(csv.DictReader(io.StringIO(filtered[FILENAMES[4]].decode("utf-8-sig"))))), 0)

    def test_context_is_authoritative_and_unvisited_assignment_is_retained(self):
        response = self.client.get(self.api("advanced/"), {**self.query, "view": "session", "session_id": self.attempt.session_id})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["view"], "session")
        self.assertEqual(payload["workspace"]["session"]["question_count"], 1)
        self.assertEqual(payload["workspace"]["responses"][0]["visit_count"], 0)
        self.assertEqual(payload["workspace"]["responses"][0]["question_ref"], "Q001")
        self.assertEqual(payload["workspace"]["responses"][0]["question_type"], "简答题")
        self.assertEqual(payload["tables"][0]["total_items"], 1)

    def test_session_preview_uses_course_and_duration_columns(self):
        response = self.client.get(self.api("advanced/"), {**self.query, "table": "sessions"})
        self.assertEqual([column['name'] for column in response.json()['preview']['columns']],
            ['sessionID', '学号', '姓名', '课程', '实验名称', '开始时间', '结束时间', '时长(分钟)'])

    def test_session_reference_is_course_stable(self):
        broad = DataScope.from_mapping(self.course)
        narrow = DataScope.from_mapping(self.course, self.query)
        self.assertEqual(broad.session_references(), narrow.session_references())
        reference = broad.session_references()[str(self.attempt.session_id)]
        self.assertRegex(reference, r"^S\d{8}_\d{4,}$")

    def test_multi_experiment_scope_and_student_context_do_not_trim_course_files(self):
        from apps.experiments.models import Experiment, ExperimentVersion, ExperimentClassSnapshot, QuestionSnapshot
        from apps.assessment.models import ExperimentSession, StudentQuestionAssignment
        from apps.experiments.presentation_instances import get_or_create_default_item_instance
        experiment = Experiment.objects.create(course=self.course, created_by=self.teacher,
            question_bank=self.experiment.question_bank, name='第二实验')
        version = ExperimentVersion.objects.create(experiment=experiment, version_number=1, published_by=self.teacher)
        snapshot = ExperimentClassSnapshot.objects.create(version=version, source_class_group=self.class_group,
            name_snapshot=self.class_group.name, academic_year_snapshot='2026', capture_mode='native')
        session = ExperimentSession.objects.create(version=version, student=self.student,
            class_snapshot=snapshot, class_snapshot_name=snapshot.display_name)
        question = QuestionSnapshot.objects.create(version=version, source_question=self.unassigned,
            source_sequence=2, code='SECOND', question_type='short_answer', text='第二题', correct_answer='答案')
        StudentQuestionAssignment.objects.create(session=session, snapshot=question,
            item_instance=get_or_create_default_item_instance(question), assigned_sequence_index=1)
        scope = {'experiment_ids': [self.experiment.pk, experiment.pk], 'class_id': self.class_group.pk}
        saved = self.client.post(self.api('scope/'), scope, content_type='application/json')
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()['current']['experiment_ids'], scope['experiment_ids'])
        payload = self.client.get(self.api('advanced/'), {'view': 'student', 'student_id': self.student.pk,
            'session_ref': DataScope.from_mapping(self.course).session_references()[str(session.pk)]}).json()
        self.assertEqual(len(payload['workspace']['sessions']), 2)
        self.assertEqual(payload['workspace']['session']['question_count'], 1)
        self.assertEqual(payload['workspace']['responses'][0]['question_ref'], 'Q002')
        self.assertEqual(payload['workspace']['student']['experiment_count'], 2)
        self.assertEqual(next(item for item in payload['tables'] if item['key'] == 'sessions')['total_items'], 1)
        self.assertEqual(next(item for item in payload['tables'] if item['key'] == 'responses')['total_items'], 1)
        selected = DataScope.from_mapping(self.course, scope)
        frozen = create_export_snapshot(course=self.course, created_by=self.teacher, scope=selected, package='experiment')
        self.assertEqual(len(frozen.selected_versions), 2)
        from urllib.parse import parse_qs, urlparse
        from .api.views import _export_job_payload, _recent_export_payload
        job = create_export_job(snapshot=frozen, course=self.course, package='experiment', created_by=self.teacher)
        history_query = parse_qs(urlparse(_export_job_payload(job)['history_url']).query)
        self.assertEqual(history_query['experiment_ids'], [','.join(map(str, selected.experiment_ids))])
        self.assertEqual(_recent_export_payload(job)['experiment_name'], self.experiment.name + '、' + experiment.name)
        import base64
        rows = list(csv.DictReader(io.StringIO(base64.b64decode(frozen.source_bundle['research_files'][FILENAMES[4]]).decode('utf-8-sig'))))
        self.assertEqual({row['课程内题目ID'] for row in rows}, {'Q001', 'Q002'})

    def test_session_cards_are_contextual_but_library_entry_and_export_are_course_wide(self):
        context = self.client.get(self.api('advanced/'), {**self.query, 'view': 'session', 'session_id': self.attempt.session_id}).json()
        self.assertEqual(next(item for item in context['tables'] if item['key'] == 'questions')['total_items'], 1)
        library = self.client.get(self.api('advanced/'), {**self.query, 'table': 'questions'}).json()
        self.assertEqual(library['preview']['page']['total_items'], 2)
        exports = self.client.get(self.api('exports/'), {**self.query, 'session_id': self.attempt.session_id}).json()
        self.assertEqual(next(item for item in exports['tables'] if item['key'] == 'question_library')['total_items'], 2)

    def test_removed_enrollment_is_retained_in_roster_for_existing_session(self):
        CourseEnrollment.objects.filter(course=self.course, student=self.student).delete()
        files = self.export_files()
        roster = list(csv.DictReader(io.StringIO(files[FILENAMES[0]].decode('utf-8-sig'))))
        self.assertEqual({row['学号'] for row in roster}, {'DATA-001', '00002'})

    def test_foreign_session_reference_is_rejected_and_question_focus_does_not_filter_exports(self):
        response = self.client.get(self.api('advanced/'), {**self.query, 'session_ref': 'S20260101_9999'})
        self.assertEqual(response.status_code, 404)
        files = self.export_files(question_ref='Q002', visit='999')
        rows = list(csv.DictReader(io.StringIO(files[FILENAMES[4]].decode('utf-8-sig'))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['课程内题目ID'], 'Q001')
