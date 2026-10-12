"""System integration baseline from the supplied 193-item source, not a validity study."""
import csv
from collections import Counter
from pathlib import Path
import uuid

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.experiments.models import Experiment, ExperimentVersion, QuestionSnapshot
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.question_bank.models import Question, QuestionBank
from .models import AssessmentEvent, Attempt, ExperimentSession, StudentQuestionAssignment
from .recording_capture_services import ensure_capture_contract_v2
from .response_snapshot_services import ResponseState
from .capture_v2_services import ingest_capture_batch


class EdaCaptureBaselineTests(TestCase):
    def test_all_193_items_have_replayable_initial_and_clear_transitions(self):
        with Path(__file__).with_name('fixtures').joinpath('eda_chapter1_193.csv').open(encoding='utf-8-sig', newline='') as source:
            rows = list(csv.DictReader(source))
        self.assertEqual(Counter(row['题型'] for row in rows), {'单选题': 95, '判断题': 41, '多选题': 34, '填空题': 19, '简答题': 4})
        self.assertEqual(len({row['序号'] for row in rows}), 193)
        teacher = User.objects.create_user(username='eda-teacher', role='teacher')
        student = User.objects.create_user(username='eda-student', role='student', student_number='EDA-001')
        bank = QuestionBank.objects.create(owner=teacher, name='193题结构基线')
        experiment = Experiment.objects.create(owner=teacher, question_bank=bank, name='短响应采集基线')
        version = ExperimentVersion.objects.create(experiment=experiment, version_number=1, published_by=teacher)
        session = ExperimentSession.objects.create(version=version, student=student, capture_contract=ensure_capture_contract_v2())
        kinds = {'单选题': 'single_choice', '判断题': 'true_false', '多选题': 'multiple_choice', '填空题': 'fill_blank', '简答题': 'short_answer'}
        stream = uuid.uuid4()
        sequence = 0
        for row in rows:
            kind = kinds[row['题型']]
            options = [{'key': key, 'text': row[f'选项{key}']} for key in 'ABCDEF' if row[f'选项{key}']]
            source_question = Question.objects.create(bank=bank, sequence=int(row['序号']), question_type=kind,
                text=row['题目'], correct_answer=row['答案'])
            snapshot = QuestionSnapshot.objects.create(version=version, source_question=source_question, source_sequence=int(row['序号']),
                code=f"EDA-{row['序号']}", question_type=kind, text=row['题目'], options=options,
                correct_answer=row['答案'], explanation=row['解析 '], primary_kc=row['主要知识点'])
            assignment = StudentQuestionAssignment.objects.create(session=session, snapshot=snapshot,
                item_instance=get_or_create_default_item_instance(snapshot), assigned_sequence_index=int(row['序号']))
            attempt = Attempt.objects.create(session=session, assignment=assignment)
            empty = [] if kind == 'multiple_choice' else None if kind in {'single_choice', 'true_false'} else ''
            response = ['A'] if kind == 'multiple_choice' else 'A' if kind == 'single_choice' else 'true' if kind == 'true_false' else '已确认的中文答案'
            parent = None
            events = []
            for before, after in [(empty, response), (response, empty)]:
                sequence += 1
                event_id = str(uuid.uuid4())
                events.append({'event_id': event_id, 'event_type': 'response_transition', 'event_version': 1,
                    'session_id': session.pk, 'attempt_id': attempt.pk, 'client_sequence_no': sequence,
                    'client_wall_time_utc': timezone.now().isoformat(), 'client_monotonic_ms': sequence * 10,
                    'client_time_origin': '2026-09-06T00:00:00Z', 'quality_flags': [], 'app_version': 'capture-v2',
                    'payload': {'capture_version': 'capture-v2', 'operation_id': str(uuid.uuid4()), 'branch_id': str(stream),
                        'parent_event_id': parent, 'input_type': 'clear' if after == empty else 'insertText', 'source': 'student',
                        'before_state': ResponseState(kind, before).state_json, 'after_state': ResponseState(kind, after).state_json,
                        'before_state_hash': ResponseState(kind, before).state_hash, 'after_state_hash': ResponseState(kind, after).state_hash}})
                parent = event_id
            ingest_capture_batch(student=student, document={'client_instance_id': str(stream), 'events': events})
        self.assertEqual(Attempt.objects.filter(session=session).count(), 193)
        self.assertEqual(AssessmentEvent.objects.filter(session=session, event_type='response_transition').count(), 386)
        self.assertEqual(sum(not row['主要知识点'].strip() for row in rows), 30)
