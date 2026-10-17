from apps.api.test_support import domain_payload
import csv
import io
import os
import tempfile
import zipfile
from uuid import uuid4
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.analytics import test_teacher_data_center_api as fixtures
from apps.analytics.data_scope import DataScope
from apps.analytics.models import ExportSnapshot
from apps.analytics.services import create_export_job, create_export_snapshot, run_export_job
from apps.assessment.models import Attempt, ConfidenceAssignment, ExperimentSession, StudentQuestionAssignment, SubmissionEvent
from apps.experiments.models import ExperimentClassSnapshot, ExperimentVersion, QuestionSnapshot
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.question_bank.models import Concept, Question
from apps.question_bank.services.question_kc_mapping import replace_question_kc_mapping


class SinglePassList(list):
    def __init__(self, values):
        super().__init__(values)
        self.iterations = 0

    def __iter__(self):
        self.iterations += 1
        if self.iterations > 1:
            raise AssertionError("collection was scanned more than once")
        return super().__iter__()


class CountingQuestionRef(str):
    comparisons = 0

    def __eq__(self, other):
        type(self).comparisons += 1
        return super().__eq__(other)

    __hash__ = str.__hash__


class ResearchDatasetTests(TestCase):
    def setUp(self):
        fixtures.TeacherDataCenterApiTests.setUp(self)
        self.query = {"experiment_id": self.experiment.pk, "class_id": self.class_group.pk}

    def api(self, suffix):
        return f"/api/v1/teacher/courses/{self.course.pk}/data/{suffix}"

    def test_unstarted_prepared_session_is_not_a_course_research_binding(self):
        from .research_dataset import read_research_bundle
        prepared, _ = self.another_session()
        ExperimentSession.objects.filter(pk=prepared.pk).update(
            status=ExperimentSession.Status.PREPARED, started_at=None, assessment_flow_version=1)
        scope = DataScope.from_mapping(self.course, self.query)
        bundle, _ = read_research_bundle(scope)
        self.assertEqual(len(bundle['files']['session_bindings']), 1)
        self.assertEqual(len(bundle['sessions']), 1)
        prepared.refresh_from_db()
        self.assertIsNone(prepared.started_at)

    def rows(self, table, **filters):
        preview = domain_payload(self.client.get(self.api("advanced/"), {**self.query, "table": table, **filters}))["preview"]
        return [dict(zip((column["name"] for column in preview["columns"]), row)) for row in preview["rows"]]

    def another_session(self, *, question_text="不同冻结题干", kc_description="不同定义", capture_contract=None):
        version = ExperimentVersion.objects.create(experiment=self.experiment, version_number=2, published_by=self.teacher)
        class_snapshot = ExperimentClassSnapshot.objects.create(version=version, source_class_group=self.class_group,
            name_snapshot=self.class_group.name, academic_year_snapshot=self.class_group.academic_year,
            capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE)
        question = QuestionSnapshot.objects.create(version=version, source_question=self.attempt.assignment.snapshot.source_question,
            source_sequence=1, code="DATA-Q1", question_type="short_answer", text=question_text, correct_answer="另一个答案",
            root_kc_code="KC1", kc_mapping_schema_version="question-kc-tree-v2", kc_mapping_snapshot={"nodes": [
                {"kc_code": "KC1", "name": "知识", "description": kc_description, "parent_kc_code": None, "depth": 1, "order": 1}]})
        session = ExperimentSession.objects.create(version=version, student=self.student, class_snapshot=class_snapshot,
            class_snapshot_name=class_snapshot.display_name, capture_contract=capture_contract)
        assignment = StudentQuestionAssignment.objects.create(session=session, snapshot=question,
            item_instance=get_or_create_default_item_instance(question), assigned_sequence_index=1)
        return session, assignment

    def test_preview_requires_experiment_and_class_without_creating_snapshot(self):
        before = ExportSnapshot.objects.count()
        payload = domain_payload(self.client.get(self.api("advanced/"), {"experiment_id": self.experiment.pk}))
        self.assertIsNone(payload["preview"])
        self.assertFalse(payload["scope"]["is_complete"])
        self.assertEqual(ExportSnapshot.objects.count(), before)

    def test_overview_without_complete_scope_does_not_mix_course_counts(self):
        payload = domain_payload(self.client.get(self.api("overview/")))
        self.assertFalse(payload["scope"]["is_complete"])
        self.assertEqual(payload["facts"]["total_sessions"], 0)
        self.assertEqual(payload["facts"]["observed_participants"], 0)

    def test_search_only_url_uses_saved_experiment_and_class(self):
        self.client.post(self.api("scope/"), self.query, content_type="application/json")
        payload = domain_payload(self.client.get(self.api("advanced/"), {"search": "DATA-001"}))
        self.assertTrue(payload["scope"]["is_complete"])
        self.assertEqual(payload["preview"]["page"]["total_items"], 1)
        self.assertIn("DATA-001", payload["scope"]["display"])

    def test_preview_has_six_research_tables_and_explicit_chinese_columns(self):
        response = self.client.get(self.api("advanced/"), {**self.query, "table": "attempts"})
        self.assertEqual(response.status_code, 200)
        payload = domain_payload(response)
        self.assertEqual([item["key"] for item in payload["tables"]], [
            "sessions", "questions", "knowledge_points", "question_kc_mapping", "responses", "behavior_events",
        ])
        self.assertEqual(payload["selected_table"]["key"], "responses")
        names = [item["name"] for item in payload["preview"]["columns"]]
        self.assertIn("首次访问顺序", names)
        self.assertIn("最终答案", names)
        self.assertNotIn("attempt_id", names)
        self.assertNotIn("groups", payload)
        row = dict(zip(names, payload["preview"]["rows"][0]))
        self.assertEqual(row["学号"], self.student.student_number)
        self.assertEqual(row["答案状态"], "未访问")
        self.assertEqual(row["最终答案"], "")

    def test_new_catalog_only_offers_experiment_package(self):
        payload = domain_payload(self.client.get(self.api("exports/"), self.query))
        self.assertEqual([item["key"] for item in payload["packages"]], ["experiment"])
        self.assertEqual(len(payload["tables"]), 8)
        self.assertFalse(payload["selected_package"]["blocked"])
        self.assertEqual(payload["export_versions"], [])

    def test_published_snapshots_reuse_course_identity_without_definition_variants(self):
        self.another_session()
        questions = self.rows("questions")
        responses = self.rows("responses")
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["题号"], "Q001")
        self.assertEqual({row["课程内题目ID"] for row in responses}, {"Q001"})
        self.assertEqual(len({row["sessionID"] for row in responses}), 2)
        self.assertTrue(all(row["答案状态"] == "未访问" for row in responses))

    def test_student_and_session_filters_are_shared_and_do_not_renumber(self):
        self.another_session()
        all_rows = self.rows("sessions")
        reference = all_rows[1]["sessionID"]
        self.assertEqual(self.rows("sessions", session_ref=reference), [all_rows[1]])
        self.assertEqual(self.rows("sessions", search=reference), [all_rows[1]])
        self.assertEqual(self.rows("sessions", search="没有匹配"), [])
        scope = DataScope.from_mapping(self.course, {**self.query, "session_ref": reference})
        self.assertEqual(scope.sessions().count(), 1)
        self.assertEqual(scope.serialize()["session_ref"], reference)

    def test_final_answer_preserves_whitespace_and_pending_grade_is_unknown(self):
        original = "  0  \n\"原文\"\n"
        self.attempt.status = Attempt.Status.DRAFT
        self.attempt.draft_answer = original
        self.attempt.save(update_fields=["status", "draft_answer"])
        draft = self.rows("responses")[0]
        self.assertEqual(draft["最终答案"], "")
        self.assertEqual(draft["是否正确"], "")
        now = timezone.now()
        self.attempt.status = Attempt.Status.SUBMITTED
        self.attempt.final_answer = original
        self.attempt.submitted_at = now
        self.attempt.score = 0
        self.attempt.is_correct = False
        self.attempt.save(update_fields=["status", "final_answer", "submitted_at", "score", "is_correct"])
        SubmissionEvent.objects.create(attempt=self.attempt, submission_token=uuid4(), final_answer=original,
            server_received_at=now, committed_at=now, grading_status="pending")
        submitted = self.rows("responses")[0]
        self.assertEqual(submitted["最终答案"], original)
        self.assertEqual(submitted["是否正确"], "")
        self.assertRegex(submitted["正式提交时间"], r"^\d{8}T\d{6}\.\d{3}$")

    def test_missing_course_mapping_is_not_inferred_from_published_legacy_names(self):
        question = self.attempt.assignment.snapshot
        question.all_kc_snapshots = [{"concept_id": 987654, "name": "旧知识点", "path": "章节 / 旧知识点", "is_primary": True}]
        question.save(update_fields=["all_kc_snapshots"])
        mapping = self.rows("question_kc_mapping")[0]
        for key in ("一级KC编号", "二级KC编号", "三级KC继承映射"):
            self.assertEqual(mapping[key], "")

    def test_unassigned_release_candidate_is_excluded(self):
        from apps.experiments.models import ReleaseSnapshotQuestionMapping

        candidate = QuestionSnapshot.objects.create(version=self.version, source_question=self.attempt.assignment.snapshot.source_question,
            source_sequence=2, code="NEVER-ASSIGNED", question_type="short_answer", text="未分配候选题", correct_answer="答案")
        ReleaseSnapshotQuestionMapping.objects.create(release_snapshot=self.version, source_question=candidate.source_question, question_snapshot=candidate)
        self.assertEqual([row["题号"] for row in self.rows("questions")], ["Q001"])

    def test_question_library_uses_current_course_definition_with_typed_answer(self):
        question = self.attempt.assignment.snapshot.source_question
        question.correct_answer = {"answer": "  标准答案\n"}
        question.save(update_fields=["correct_answer"])
        import json
        self.assertEqual(json.loads(self.rows("questions")[0]["答案"]), {"answer": "  标准答案\n"})

    def test_research_bundle_bulk_loads_question_mappings_without_changing_order(self):
        from .research_dataset import read_research_bundle

        root = Concept.objects.create(course=self.course, code="KC1", name="根知识点")
        branch = Concept.objects.create(course=self.course, code="KC2", name="二级知识点")
        leaf = Concept.objects.create(course=self.course, code="KC3", name="三级知识点")
        questions = [self.attempt.assignment.snapshot.source_question]
        questions.extend(
            Question.objects.create(
                bank=self.experiment.question_bank,
                sequence=sequence,
                question_type=Question.Type.SHORT,
                text=f"数据题 {sequence}",
                correct_answer="答案",
            )
            for sequence in (2, 3)
        )
        replace_question_kc_mapping(
            questions[0],
            root_kc_code=root.code,
            nodes=[{"kc_code": root.code, "parent_kc_code": None, "order": 1}],
        )
        replace_question_kc_mapping(
            questions[1],
            root_kc_code=root.code,
            nodes=[
                {"kc_code": root.code, "parent_kc_code": None, "order": 1},
                {"kc_code": branch.code, "parent_kc_code": root.code, "order": 1},
            ],
        )
        replace_question_kc_mapping(
            questions[2],
            root_kc_code=root.code,
            nodes=[
                {"kc_code": root.code, "parent_kc_code": None, "order": 1},
                {"kc_code": branch.code, "parent_kc_code": root.code, "order": 1},
                {"kc_code": leaf.code, "parent_kc_code": branch.code, "order": 1},
            ],
        )
        scope = DataScope.from_mapping(self.course, self.query)

        with CaptureQueriesContext(connection) as queries:
            bundle, _now = read_research_bundle(scope)

        mapping_queries = [
            query["sql"]
            for query in queries.captured_queries
            if '"question_bank_questionconcept"' in query["sql"]
        ]
        self.assertLessEqual(len(mapping_queries), 1)
        self.assertEqual(
            bundle["files"]["course_mapping"],
            [
                [self.course.name, "Q001", "KC1", "", ""],
                [self.course.name, "Q002", "KC1", "KC2", ""],
                [self.course.name, "Q003", "KC1", "KC2", "KC2>KC3"],
            ],
        )

    def test_export_capture_does_not_fetch_submission_event_per_attempt(self):
        from .research_dataset import read_research_bundle

        session = self.attempt.session
        now = timezone.now()
        attempts = [self.attempt]
        for sequence in (2, 3, 4):
            snapshot = QuestionSnapshot.objects.create(
                version=self.version,
                source_question=self.attempt.assignment.snapshot.source_question,
                source_sequence=sequence,
                code=f"DATA-Q{sequence}",
                question_type=Question.Type.SHORT,
                text=f"冻结题 {sequence}",
                correct_answer="答案",
            )
            assignment = StudentQuestionAssignment.objects.create(
                session=session,
                snapshot=snapshot,
                item_instance=get_or_create_default_item_instance(snapshot),
                assigned_sequence_index=sequence,
            )
            attempts.append(Attempt.objects.create(session=session, assignment=assignment))
        for attempt in attempts:
            SubmissionEvent.objects.create(
                attempt=attempt,
                submission_token=uuid4(),
                final_answer="答案",
                server_received_at=now,
                committed_at=now,
            )
        scope = DataScope.from_mapping(self.course, self.query)

        with CaptureQueriesContext(connection) as queries:
            read_research_bundle(scope)

        per_attempt_submission_queries = [
            query["sql"]
            for query in queries.captured_queries
            if 'FROM "assessment_submissionevent"' in query["sql"]
            and '"assessment_submissionevent"."attempt_id" =' in query["sql"]
            and "LIMIT 21" in query["sql"]
        ]
        self.assertEqual(per_attempt_submission_queries, [])

    def test_research_bundle_indexes_visits_and_session_counts_once(self):
        from .research_dataset import read_research_bundle

        second_session, _assignment = self.another_session()
        first_session = self.attempt.session
        for sequence in (2, 3, 4):
            question = Question.objects.create(
                bank=self.experiment.question_bank,
                sequence=sequence,
                question_type=Question.Type.SHORT,
                text=f"索引题 {sequence}",
                correct_answer="答案",
            )
            snapshot = QuestionSnapshot.objects.create(
                version=self.version,
                source_question=question,
                source_sequence=sequence,
                code=f"INDEX-Q{sequence}",
                question_type=Question.Type.SHORT,
                text=question.text,
                correct_answer=question.correct_answer,
            )
            StudentQuestionAssignment.objects.create(
                session=first_session,
                snapshot=snapshot,
                item_instance=get_or_create_default_item_instance(snapshot),
                assigned_sequence_index=sequence,
            )
        question_refs = {
            sequence: CountingQuestionRef(f"Q{sequence:03d}")
            for sequence in range(1, 5)
        }
        visit_rows = SinglePassList(
            [
                {
                    "session_id": str(first_session.pk),
                    "question_ref": question_refs[sequence],
                    "clicked_at": "",
                    "entered_at": "",
                    "left_at": "",
                }
                for sequence in (4, 3, 2, 1, 4)
            ]
        )
        behavior_rows = SinglePassList(
            [
                {"session_id": str(first_session.pk), "row": ["first"]},
                {"session_id": str(second_session.pk), "row": ["second"]},
            ]
        )
        scope = DataScope.from_mapping(self.course, self.query)
        CountingQuestionRef.comparisons = 0

        with patch("apps.analytics.research_dataset.build_visits", return_value=visit_rows), patch(
            "apps.analytics.research_events.build_standard_behavior",
            return_value=behavior_rows,
        ):
            bundle, _now = read_research_bundle(scope)

        self.assertEqual(visit_rows.iterations, 1)
        self.assertEqual(behavior_rows.iterations, 1)
        self.assertLess(CountingQuestionRef.comparisons, 16)
        self.assertEqual(
            [row["first_visit_order"] for row in bundle["responses"]],
            [4, 3, 2, 1, None],
        )
        self.assertIs(bundle["behavior"], behavior_rows)
        refs = scope.session_references()
        self.assertEqual(
            {
                row["session_ref"]: (row["question_count"], row["behavior_count"])
                for row in bundle["sessions"]
            },
            {
                refs[str(first_session.pk)]: (4, 1),
                refs[str(second_session.pk)]: (1, 1),
            },
        )

    def test_single_choice_capture_chain_and_transport_retry_match_final_answer(self):
        from apps.assessment.capture_v2_services import ingest_capture_batch
        from apps.assessment.models import AssessmentEvent
        from apps.assessment.recording_capture_services import ensure_capture_contract_v2
        from apps.assessment.response_snapshot_services import ResponseState

        session, assignment = self.another_session(capture_contract=ensure_capture_contract_v2())
        self.attempt = Attempt.objects.create(session=session, assignment=assignment)
        question = assignment.snapshot
        question.question_type = "single_choice"
        question.options = [{"key": value, "text": value} for value in "ABC"]
        question.correct_answer = "C"
        question.save(update_fields=["question_type", "options", "correct_answer"])
        client_id, branch = uuid4(), uuid4()
        parent, before = None, None
        documents = []
        for sequence, after in enumerate("ABC", 1):
            event_id = str(uuid4())
            document = {
                "event_id": event_id, "event_type": "response_transition", "event_version": 1,
                "session_id": session.pk, "attempt_id": self.attempt.pk, "client_sequence_no": sequence,
                "client_wall_time_utc": timezone.now().isoformat(), "client_monotonic_ms": sequence * 100,
                "client_time_origin": "2026-09-09T00:00:00Z", "quality_flags": [], "app_version": "capture-v2",
                "payload": {"capture_version": "capture-v2", "operation_id": str(uuid4()), "branch_id": str(branch),
                    "parent_event_id": parent, "before_state": ResponseState("single_choice", before).state_json,
                    "after_state": ResponseState("single_choice", after).state_json,
                    "before_state_hash": ResponseState("single_choice", before).state_hash,
                    "after_state_hash": ResponseState("single_choice", after).state_hash,
                    "input_type": "change", "option_key": after, "option_action": "select", "source": "student"},
            }
            ingest_capture_batch(student=self.student, document={"client_instance_id": str(client_id), "events": [document]})
            documents.append(document)
            parent, before = event_id, after
        ingest_capture_batch(student=self.student, document={"client_instance_id": str(client_id), "events": [documents[-1]]})
        final_event = AssessmentEvent.objects.get(event_id=parent)
        now = timezone.now()
        self.attempt.active_response_event = final_event
        self.attempt.final_response_event = final_event
        self.attempt.current_response_snapshot_id = final_event.payload["after_snapshot_id"]
        self.attempt.final_response_snapshot_id = final_event.payload["after_snapshot_id"]
        self.attempt.final_answer = "C"
        self.attempt.status = Attempt.Status.SUBMITTED
        self.attempt.submitted_at = now
        self.attempt.save(update_fields=["active_response_event", "final_response_event", "current_response_snapshot",
            "final_response_snapshot", "final_answer", "status", "submitted_at"])
        SubmissionEvent.objects.create(attempt=self.attempt, submission_token=uuid4(), final_answer="C",
            final_response_snapshot_id=self.attempt.final_response_snapshot_id, server_received_at=now, committed_at=now)
        behaviors = self.rows("behavior_events")
        transitions = [row for row in behaviors if row["行为类别"] == "答案交互"]
        self.assertEqual([(row["变化前内容"], row["变化后内容"]) for row in transitions], [("", "A"), ("A", "B"), ("B", "C")])
        self.assertTrue(all(row["发生时间"] for row in transitions))
        self.assertTrue(all("正式提交链" in row["补充信息"] for row in transitions))
        self.assertEqual(len([row for row in behaviors if row["具体行为"] == "正式提交记录"]), 1)
        response = self.rows("responses")[-1]
        self.assertEqual(response["最终答案"], "C")

    def test_quality_and_history_are_readable_and_scoped(self):
        response = self.client.get(self.api("quality/"), self.query)
        self.assertEqual(response.status_code, 200)
        payload = domain_payload(response)
        self.assertEqual(payload["items"][0]["student_number"], "DATA-001")
        self.assertTrue(payload["items"][0]["session_ref"].startswith("S"))
        self.assertIn("无法评估", payload["counts"])
        self.assertNotIn("entity_id", payload["items"][0])
        self.assertNotIn("rule", payload["items"][0])

    def test_frozen_package_matches_preview_and_survives_live_edits(self):
        scope = DataScope.from_mapping(self.course, self.query)
        snapshot = create_export_snapshot(course=self.course, experiment=self.experiment,
                                          created_by=self.teacher, scope=scope, package="experiment")
        job = create_export_job(course=self.course, experiment=self.experiment,
                                created_by=self.teacher, snapshot=snapshot, package="experiment")
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"EXPORT_ROOT": directory}):
            run_export_job(job)
            with zipfile.ZipFile(job.artifact_path) as archive:
                from .research_contract import FILES
                expected = {item["path"] for item in FILES}
                self.assertEqual(set(archive.namelist()), expected)
                original = {name: archive.read(name) for name in archive.namelist()}
                for key, filename in (("knowledge_points", "03_课程知识点表.csv"), ("question_kc_mapping", "04_题目KC映射表.csv"),
                                      ("responses", "05_单次实验_逐题作答表.csv"), ("behavior_events", "06_单次实验_行为过程表.csv")):
                    preview = domain_payload(self.client.get(self.api("advanced/"), {**self.query, "table": key}))["preview"]
                    parsed = list(csv.reader(io.StringIO(original[filename].decode("utf-8-sig"))))
                    self.assertEqual(parsed[0], [column["name"] for column in preview["columns"]])
                    self.assertEqual(parsed[1:], [[str(v) if v is not None else "" for v in row] for row in preview["rows"]])
            self.student.display_name = "后改姓名"
            self.student.save(update_fields=["display_name"])
            self.attempt.draft_answer = "  后改答案\n"
            self.attempt.save(update_fields=["draft_answer"])
            question = self.attempt.assignment.snapshot
            question.text = "冻结后修改题干"
            question.save(update_fields=["text"])
            from apps.assessment.event_services import append_assessment_event
            append_assessment_event(event_type="window_blurred", event_version=1,
                                    event_domain="telemetry", actor_source="server",
                                    session=self.attempt.session, payload={})
            run_export_job(job)
            with zipfile.ZipFile(job.artifact_path) as archive:
                self.assertEqual(original, {name: archive.read(name) for name in archive.namelist()})
