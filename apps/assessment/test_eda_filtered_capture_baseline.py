"""Integration baseline for the supplied 112-item filtered EDA source."""
import csv
from collections import Counter
from pathlib import Path
import uuid

from django.test import TestCase
from django.utils import timezone

from apps.accounts.enrollment_access import open_enrollment_access
from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.experiments.models import Experiment, ExperimentVersion, QuestionSnapshot
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.experiments.publication_services import _create_question_snapshot
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept
from apps.question_bank.services.import_parsing import (
    parse_question_kc_tree_rows,
    parse_question_rows,
)
from apps.question_bank.services.import_service import import_questions
from apps.question_bank.services.question_kc_import import (
    apply_question_kc_tree_import,
    preview_question_kc_trees,
)
from apps.question_bank.services.question_kc_mapping import project_question_kc_mapping

from .capture_v2_services import ingest_capture_batch
from .models import AssessmentEvent, Attempt, ExperimentSession, StudentQuestionAssignment
from .recording_capture_services import ensure_capture_contract_v2
from .response_snapshot_services import ResponseState


class EdaFilteredCaptureBaselineTests(TestCase):
    fixtures_path = Path(__file__).with_name("fixtures")

    @classmethod
    def _read_rows(cls, filename):
        with cls.fixtures_path.joinpath(filename).open(encoding="utf-8-sig", newline="") as source:
            return list(csv.DictReader(source))

    @staticmethod
    def _transition(*, session, attempt, sequence, before, after, parent, branch_id):
        question_type = attempt.assignment.snapshot.question_type
        return {
            "event_id": str(uuid.uuid4()),
            "event_type": "response_transition",
            "event_version": 1,
            "session_id": session.pk,
            "attempt_id": attempt.pk,
            "client_sequence_no": sequence,
            "client_wall_time_utc": timezone.now().isoformat(),
            "client_monotonic_ms": sequence * 10,
            "client_time_origin": "2026-09-06T00:00:00Z",
            "quality_flags": [],
            "app_version": "capture-v2",
            "payload": {
                "capture_version": "capture-v2",
                "operation_id": str(uuid.uuid4()),
                "branch_id": str(branch_id),
                "parent_event_id": parent,
                "input_type": "clear" if after == _empty_response(question_type) else "insertText",
                "source": "student",
                "before_state": ResponseState(question_type, before).state_json,
                "after_state": ResponseState(question_type, after).state_json,
                "before_state_hash": ResponseState(question_type, before).state_hash,
                "after_state_hash": ResponseState(question_type, after).state_hash,
            },
        }

    def test_filtered_rows_keep_their_own_sequences_through_kc_freeze_and_raw_capture(self):
        question_rows = self._read_rows("eda_chapter1_filtered_questions.csv")
        kc_rows = self._read_rows("eda_chapter1_filtered_kc_tree.csv")
        original_rows = self._read_rows("eda_chapter1_193.csv")
        parsed_questions = parse_question_rows(question_rows)
        parsed_kc_trees = parse_question_kc_tree_rows(kc_rows)
        sequences = set(range(1, 113))

        self.assertEqual(len(parsed_questions), 112)
        self.assertEqual(
            Counter(question.question_type for question in parsed_questions),
            {
                Question.Type.SINGLE: 78,
                Question.Type.MULTIPLE: 30,
                Question.Type.SHORT: 4,
            },
        )
        self.assertEqual({question.sequence for question in parsed_questions}, sequences)
        self.assertEqual(len(parsed_kc_trees), 112)
        self.assertEqual({mapping.sequence for mapping in parsed_kc_trees}, sequences)
        self.assertEqual(sum(bool(mapping.root) for mapping in parsed_kc_trees), 112)
        self.assertEqual(sum(bool(mapping.level_two) for mapping in parsed_kc_trees), 85)
        self.assertEqual(sum(bool(mapping.level_three) for mapping in parsed_kc_trees), 62)

        original_sequence_by_text = {row["题目"]: int(row["序号"]) for row in original_rows}
        self.assertTrue(all(question.text in original_sequence_by_text for question in parsed_questions))
        renumbered = [
            question.sequence
            for question in parsed_questions
            if question.sequence != original_sequence_by_text[question.text]
        ]
        self.assertEqual(len(renumbered), 99)

        teacher = User.objects.create_user(username="eda-filtered-teacher", role=User.Role.TEACHER)
        student = User.objects.create_user(
            username="eda-filtered-student",
            role=User.Role.STUDENT,
            student_number="EDA-FILTERED-001",
        )
        course = Course.objects.create(
            owner=teacher,
            name="EDA 筛选题集验证课程",
            code="EDA-FILTERED",
            term="2026 验收",
        )
        class_group = ClassGroup.objects.create(course=course, name="EDA 验收班")
        enrollment = CourseEnrollment.objects.create(
            course=course,
            class_group=class_group,
            student=student,
        )
        open_enrollment_access(enrollment)
        concept_names = {
            name
            for mapping in parsed_kc_trees
            for name in (
                [mapping.root, *mapping.level_two]
                + [parent for parent, _child in mapping.level_three]
                + [child for _parent, child in mapping.level_three]
            )
        }
        concepts_by_name = {
            name: Concept.objects.create(course=course, name=name)
            for name in sorted(concept_names)
        }
        self.assertEqual(Concept.objects.filter(course=course).count(), len(concept_names))

        bank = QuestionBank.objects.create(
            owner=teacher,
            course=course,
            name="EDA 筛选后 112 题",
        )
        imported = import_questions(bank, parsed_questions)
        self.assertEqual(len(imported), 112)
        questions_by_sequence = {
            question.sequence: question
            for question in Question.objects.filter(bank=bank).order_by("sequence")
        }
        self.assertEqual(set(questions_by_sequence), sequences)

        preview = preview_question_kc_trees(bank=bank, parsed_rows=parsed_kc_trees)
        self.assertEqual(preview["schema_version"], "question-kc-tree-v2")
        self.assertEqual(len(preview["trees"]), 112)
        preview_by_sequence = {tree["sequence"]: tree for tree in preview["trees"]}
        mappings_by_sequence = {mapping.sequence: mapping for mapping in parsed_kc_trees}
        parsed_questions_by_sequence = {question.sequence: question for question in parsed_questions}
        for sequence in sequences:
            question = questions_by_sequence[sequence]
            mapping = mappings_by_sequence[sequence]
            tree = preview_by_sequence[sequence]
            self.assertEqual(question.text, parsed_questions_by_sequence[sequence].text)
            self.assertEqual(tree["question_id"], question.pk)
            self.assertEqual(tree["root_kc_code"], concepts_by_name[mapping.root].code)
        for sequence in renumbered:
            self.assertNotEqual(
                sequence,
                original_sequence_by_text[questions_by_sequence[sequence].text],
            )

        applied = apply_question_kc_tree_import(bank=bank, payload=preview)
        self.assertEqual(applied["questions_updated"], 112)
        self.assertEqual(QuestionConcept.objects.filter(question__bank=bank, is_primary=True).count(), 112)

        source_mappings = {}
        expected_link_count = 0
        for sequence in sequences:
            question = questions_by_sequence[sequence]
            projection = project_question_kc_mapping(question)
            expected_tree = preview_by_sequence[sequence]
            expected_link_count += len(expected_tree["nodes"])
            self.assertEqual(projection["root_kc_code"], expected_tree["root_kc_code"])
            self.assertEqual(
                {
                    (node["kc_code"], node["parent_kc_code"], node["order"])
                    for node in projection["nodes"]
                },
                {
                    (node["kc_code"], node["parent_kc_code"], node["order"])
                    for node in expected_tree["nodes"]
                },
            )
            self.assertEqual(len(projection["mapping_hash"]), 64)
            source_mappings[sequence] = projection
        self.assertEqual(QuestionConcept.objects.filter(question__bank=bank).count(), expected_link_count)

        experiment = Experiment.objects.create(
            owner=teacher,
            course=course,
            question_bank=bank,
            name="EDA 筛选题集采集基线",
        )
        version = ExperimentVersion.objects.create(
            experiment=experiment,
            version_number=1,
            published_by=teacher,
        )
        snapshots_by_sequence = {
            question.sequence: _create_question_snapshot(version, question)
            for question in questions_by_sequence.values()
        }
        self.assertEqual(QuestionSnapshot.objects.filter(version=version).count(), 112)
        for sequence, snapshot in snapshots_by_sequence.items():
            self.assertEqual(snapshot.kc_mapping_hash, source_mappings[sequence]["mapping_hash"])
            self.assertEqual(snapshot.kc_mapping_snapshot, source_mappings[sequence])
            self.assertEqual(snapshot.root_kc_code, source_mappings[sequence]["root_kc_code"])

        session = ExperimentSession.objects.create(
            version=version,
            student=student,
            capture_contract=ensure_capture_contract_v2(),
        )
        attempts_by_sequence = {}
        for assigned_sequence_index, sequence in enumerate(sorted(snapshots_by_sequence), start=1):
            snapshot = snapshots_by_sequence[sequence]
            assignment = StudentQuestionAssignment.objects.create(
                session=session,
                snapshot=snapshot,
                item_instance=get_or_create_default_item_instance(snapshot),
                assigned_sequence_index=assigned_sequence_index,
            )
            attempts_by_sequence[sequence] = Attempt.objects.create(session=session, assignment=assignment)
        self.assertEqual(Attempt.objects.filter(session=session).count(), 112)

        stream = uuid.uuid4()
        capture_events = []
        expected_states = {}
        sequence = 0
        for question_sequence, attempt in attempts_by_sequence.items():
            question_type = attempt.assignment.snapshot.question_type
            empty = _empty_response(question_type)
            answer = _first_response(question_type)
            branch_id = uuid.uuid4()
            sequence += 1
            first = self._transition(
                session=session,
                attempt=attempt,
                sequence=sequence,
                before=empty,
                after=answer,
                parent=None,
                branch_id=branch_id,
            )
            sequence += 1
            clear = self._transition(
                session=session,
                attempt=attempt,
                sequence=sequence,
                before=answer,
                after=empty,
                parent=first["event_id"],
                branch_id=branch_id,
            )
            capture_events.extend([first, clear])
            expected_states[attempt.pk] = (question_type, answer, empty)
        for start in range(0, len(capture_events), 100):
            ingest_capture_batch(
                student=student,
                document={"client_instance_id": str(stream), "events": capture_events[start:start + 100]},
            )

        self.assertEqual(
            AssessmentEvent.objects.filter(session=session, event_type="response_transition").count(),
            224,
        )
        for attempt in attempts_by_sequence.values():
            question_type, answer, empty = expected_states[attempt.pk]
            transitions = list(
                AssessmentEvent.objects.filter(attempt=attempt, event_type="response_transition")
                .order_by("server_ingest_sequence_no")
            )
            self.assertEqual(len(transitions), 2)
            replayed_state = transitions[0].payload["before_state"]
            parent = None
            for transition in transitions:
                self.assertEqual(transition.payload["parent_event_id"], parent)
                self.assertEqual(transition.payload["before_state"], replayed_state)
                self.assertEqual(
                    transition.payload["before_state_hash"],
                    ResponseState(question_type, replayed_state["value"]).state_hash,
                )
                replayed_state = transition.payload["after_state"]
                self.assertEqual(
                    transition.payload["after_state_hash"],
                    ResponseState(question_type, replayed_state["value"]).state_hash,
                )
                parent = str(transition.event_id)
            self.assertEqual(transitions[0].payload["after_state"], ResponseState(question_type, answer).state_json)
            self.assertEqual(replayed_state, ResponseState(question_type, empty).state_json)
            attempt.refresh_from_db()
            self.assertIsNone(attempt.active_response_event_id)
            self.assertIsNone(attempt.current_response_snapshot_id)


def _empty_response(question_type):
    return [] if question_type == Question.Type.MULTIPLE else None if question_type == Question.Type.SINGLE else ""


def _first_response(question_type):
    return ["A"] if question_type == Question.Type.MULTIPLE else "A" if question_type == Question.Type.SINGLE else "已确认的中文答案"
