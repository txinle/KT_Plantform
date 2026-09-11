from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import CourseEnrollment, User
from apps.analytics import test_teacher_data_center_api as fixtures
from apps.assessment.models import ExperimentSession
from apps.experiments.models import ExperimentClassSnapshot, ExperimentVersion

from .models import ResearchSessionBehavior, ResearchSessionMaterialization


@override_settings(
    ADVANCED_DATA_MATERIALIZED_ENABLED=True,
    ADVANCED_DATA_MATERIALIZED_COURSE_IDS=frozenset({"*"}),
)
class AdvancedMaterializedApiTests(TestCase):
    def setUp(self):
        fixtures.TeacherDataCenterApiTests.setUp(self)
        self.session = self.attempt.session

    def api(self, suffix):
        return f"/api/v1/teacher/courses/{self.course.pk}/data/{suffix}"

    def _query(self, **values):
        return {
            "experiment_id": self.experiment.pk,
            "class_id": self.class_group.pk,
            **values,
        }

    def _materialize(self, session=None):
        from .research_materialization import (
            claim_session_materialization,
            enqueue_session_materialization,
            run_session_materialization,
        )

        item = enqueue_session_materialization(session or self.session)
        claimed = claim_session_materialization(worker_id="api-test", lease_seconds=900)
        self.assertEqual(claimed.pk, item.pk)
        run_session_materialization(claimed, worker_id="api-test")
        item.refresh_from_db()
        self.assertTrue(item.is_current, item.error)
        return item

    def test_selected_table_reads_materialized_rows_without_live_bundle(self):
        item = self._materialize()

        with CaptureQueriesContext(connection) as queries:
            with patch(
                "apps.analytics.api.views.read_research_bundle",
                side_effect=AssertionError("legacy full bundle called"),
            ):
                response = self.client.get(
                    self.api("advanced/"), self._query(table="responses")
                )

        self.assertEqual(response.status_code, 200, response.content[:300])
        payload = response.json()
        self.assertEqual(payload["preview"]["page"]["total_items"], 1)
        self.assertEqual(payload["preparation"]["state"], "ready")
        self.assertEqual(payload["preparation"]["result_version"], str(item.pk))
        sql = "\n".join(query["sql"] for query in queries.captured_queries)
        self.assertLess(len(queries), 40)
        self.assertNotIn('"analytics_researchsessionvisit"', sql)
        self.assertNotIn('"analytics_researchsessionbehavior"', sql)

    def test_question_table_does_not_load_behavior_child_rows(self):
        self._materialize()

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(
                self.api("advanced/"), self._query(table="questions")
            )

        self.assertEqual(response.status_code, 200)
        sql = "\n".join(query["sql"] for query in queries.captured_queries)
        self.assertLess(len(queries), 40)
        self.assertNotIn('"analytics_researchsessionbehavior"', sql)
        self.assertEqual(response.json()["selected_table"]["key"], "questions")

    def test_standard_page_clamps_rows_and_metadata_together(self):
        self._materialize()

        response = self.client.get(
            self.api("advanced/"), self._query(table="responses", page=999)
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["preview"]["page"]["number"], 1)
        self.assertEqual(len(response.json()["preview"]["rows"]), 1)

    def test_behavior_uses_fixed_result_cursor_and_twenty_row_limit(self):
        item = self._materialize()
        ResearchSessionBehavior.objects.bulk_create(
            [
                ResearchSessionBehavior(
                    materialization=item,
                    ordinal=ordinal,
                    question_ref="Q001",
                    visit_key="Q001:1",
                    sequence=ordinal,
                    category="测试",
                    action="行为",
                    occurred_at=f"t{ordinal}",
                    row=[f"event-{ordinal}"],
                    payload={"question_ref": "Q001", "sequence": ordinal},
                )
                for ordinal in range(1, 26)
            ]
        )
        item.row_counts = {**item.row_counts, "behavior": 25}
        item.save(update_fields=["row_counts"])
        later_result = ResearchSessionMaterialization.objects.create(session=self.session)
        ResearchSessionBehavior.objects.create(
            materialization=later_result,
            ordinal=1,
            question_ref="Q001",
            visit_key="Q001:1",
            sequence=999,
            category="测试",
            action="稍后发布",
            occurred_at="later",
            row=["later-result"],
            payload={"question_ref": "Q001", "sequence": 999},
        )

        first = self.client.get(
            self.api("advanced/"), self._query(table="behavior_events")
        ).json()
        self.assertEqual(len(first["preview"]["rows"]), 20)
        self.assertIsNone(first["preview"]["cursor"]["previous"])
        next_cursor = first["preview"]["cursor"]["next"]
        self.assertTrue(next_cursor)

        phantom_student = User.objects.create_user(
            username="behavior-cursor-phantom",
            role=User.Role.STUDENT,
            student_number="CURSOR-PHANTOM",
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_group,
            student=phantom_student,
        )
        phantom_session = ExperimentSession.objects.create(
            version=self.version,
            student=phantom_student,
            class_snapshot=self.class_snapshot,
            class_snapshot_name=self.class_snapshot.display_name,
            status=ExperimentSession.Status.COMPLETED,
        )
        phantom_result = ResearchSessionMaterialization.objects.create(
            session=phantom_session,
            status=ResearchSessionMaterialization.Status.SUCCEEDED,
            is_current=True,
            activated_at=timezone.now(),
            finished_at=timezone.now(),
            row_counts={"behavior": 1},
        )
        ResearchSessionBehavior.objects.create(
            materialization=phantom_result,
            ordinal=1,
            sequence=1,
            row=["phantom-session"],
            payload={"sequence": 1},
        )

        later_result.status = later_result.Status.SUCCEEDED
        later_result.finished_at = timezone.now()
        later_result.save(update_fields=["status", "finished_at"])

        second = self.client.get(
            self.api("advanced/"),
            self._query(table="behavior_events", cursor=next_cursor),
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(second.json()["preview"]["rows"]), 5)
        self.assertNotIn(["later-result"], second.json()["preview"]["rows"])
        self.assertNotIn(["phantom-session"], second.json()["preview"]["rows"])
        self.assertEqual(
            second.json()["preview"]["cursor"]["result_version"],
            first["preview"]["cursor"]["result_version"],
        )
        fresh = self.client.get(
            self.api("advanced/"), self._query(table="behavior_events")
        )
        self.assertNotIn(["later-result"], fresh.json()["preview"]["rows"])
        rejected = self.client.get(
            self.api("advanced/"),
            self._query(table="behavior_events", cursor=next_cursor + "tampered"),
        )
        self.assertEqual(rejected.status_code, 400)

    def test_behavior_first_page_locks_the_observed_current_version_set(self):
        self._materialize()

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(
                self.api("advanced/"), self._query(table="behavior_events")
            )

        self.assertEqual(response.status_code, 200)
        materialization_queries = [
            query["sql"]
            for query in queries.captured_queries
            if '"analytics_researchsessionmaterialization"' in query["sql"]
            and "FOR UPDATE" in query["sql"]
        ]
        self.assertTrue(materialization_queries)
        session_queries = [
            query["sql"]
            for query in queries.captured_queries
            if '"assessment_experimentsession"' in query["sql"]
            and "FOR UPDATE" in query["sql"]
        ]
        self.assertTrue(session_queries)

    def test_behavior_cursor_reports_stable_page_number_for_sparse_ordinals(self):
        item = self._materialize()
        ResearchSessionBehavior.objects.bulk_create(
            [
                ResearchSessionBehavior(
                    materialization=item,
                    ordinal=ordinal,
                    question_ref="QSPARSE",
                    visit_key="QSPARSE:1",
                    sequence=ordinal,
                    row=[f"event-{ordinal}"],
                    payload={"question_ref": "QSPARSE", "sequence": ordinal},
                )
                for ordinal in range(101, 126)
            ]
        )

        first = self.client.get(
            self.api("advanced/"),
            self._query(table="behavior_events", question_ref="QSPARSE"),
        ).json()
        second = self.client.get(
            self.api("advanced/"),
            self._query(
                table="behavior_events",
                question_ref="QSPARSE",
                cursor=first["preview"]["cursor"]["next"],
            ),
        ).json()

        self.assertEqual(first["preview"]["page"]["number"], 1)
        self.assertEqual(second["preview"]["page"]["number"], 2)

    def test_student_search_is_server_limited_and_session_options_are_scoped(self):
        for index in range(25):
            student = User.objects.create_user(
                username=f"search-student-{index:02d}",
                role=User.Role.STUDENT,
                student_number=f"SEARCH-{index:02d}",
                display_name=f"检索学生 {index:02d}",
            )
            CourseEnrollment.objects.create(
                course=self.course, class_group=self.class_group, student=student
            )
            ExperimentSession.objects.create(
                version=self.version,
                student=student,
                class_snapshot=self.class_snapshot,
                class_snapshot_name=self.class_snapshot.display_name,
                status=ExperimentSession.Status.COMPLETED,
            )

        search = self.client.get(
            self.api("advanced/students/"), self._query(q="检索学生")
        )
        self.assertEqual(search.status_code, 200)
        self.assertEqual(len(search.json()["items"]), 20)

        response = self.client.get(
            self.api("advanced/"),
            self._query(view="student", student_id=self.student.pk),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            {row["student_id"] for row in payload["workspace"]["sessions"]},
            {self.student.pk},
        )
        self.assertLessEqual(len(payload["session_options"]), 20)

    def test_student_view_keeps_all_student_sessions_when_one_session_is_selected(self):
        from .advanced_materialized import _session_reference

        version = ExperimentVersion.objects.create(
            experiment=self.experiment,
            version_number=self.version.version_number + 1,
            published_by=self.teacher,
        )
        class_snapshot = ExperimentClassSnapshot.objects.create(
            version=version,
            source_class_group=self.class_group,
            name_snapshot=self.class_group.name,
            academic_year_snapshot=self.class_group.academic_year,
            capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
        )
        other = ExperimentSession.objects.create(
            version=version,
            student=self.student,
            class_snapshot=class_snapshot,
            class_snapshot_name=class_snapshot.display_name,
            status=ExperimentSession.Status.COMPLETED,
        )

        response = self.client.get(
            self.api("advanced/"),
            self._query(
                view="student",
                student_id=self.student.pk,
                session_ref=_session_reference(self.session),
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {row["session_ref"] for row in response.json()["workspace"]["sessions"]},
            {_session_reference(self.session), _session_reference(other)},
        )

    def test_student_view_keeps_an_explicit_session_older_than_display_limit(self):
        from .advanced_materialized import _session_reference

        terminal_at = timezone.now()
        versions = ExperimentVersion.objects.bulk_create(
            [
                ExperimentVersion(
                    experiment=self.experiment,
                    version_number=self.version.version_number + index + 1,
                    published_by=self.teacher,
                )
                for index in range(51)
            ]
        )
        snapshots = ExperimentClassSnapshot.objects.bulk_create(
            [
                ExperimentClassSnapshot(
                    version=version,
                    source_class_group=self.class_group,
                    name_snapshot=self.class_group.name,
                    academic_year_snapshot=self.class_group.academic_year,
                    capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
                )
                for version in versions
            ]
        )
        ExperimentSession.objects.bulk_create(
            [
                ExperimentSession(
                    version=version,
                    student=self.student,
                    class_snapshot=class_snapshot,
                    class_snapshot_name=class_snapshot.display_name,
                    status=ExperimentSession.Status.COMPLETED,
                    completed_at=terminal_at,
                    terminal_at=terminal_at,
                )
                for version, class_snapshot in zip(versions, snapshots)
            ]
        )

        response = self.client.get(
            self.api("advanced/"),
            self._query(
                view="student",
                student_id=self.student.pk,
                session_ref=_session_reference(self.session),
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["workspace"]["session"]["session_ref"],
            _session_reference(self.session),
        )
        self.assertIn(
            _session_reference(self.session),
            [item["session_ref"] for item in response.json()["workspace"]["sessions"]],
        )
