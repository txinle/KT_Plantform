import json
from unittest import skipUnless
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import User
from apps.api.test_support import domain_payload
from apps.analytics import test_teacher_data_center_api as fixtures
from apps.assessment.models import ExperimentSession
from apps.experiments.models import Experiment, ExperimentClassSnapshot, ExperimentVersion

from .advanced_materialized import materialized_advanced_payload, materialized_advanced_status
from .data_scope import DataScope
from .models import ResearchSessionMaterialization
from .research_materialization import PROJECTION_VERSION


@skipUnless(connection.vendor == "postgresql", "PostgreSQL teacher read scaling")
@override_settings(
    ADVANCED_DATA_MATERIALIZED_ENABLED=True,
    ADVANCED_DATA_MATERIALIZED_COURSE_IDS=frozenset({"*"}),
)
class TeacherReadScalingTests(TestCase):
    def setUp(self):
        fixtures.TeacherDataCenterApiTests.setUp(self)
        self.params = {"experiment_id": self.experiment.pk, "class_id": self.class_group.pk}
        self._insert_sessions(179, self.version, self.class_snapshot, prefix="selected")
        self.selected = list(ExperimentSession.objects.filter(version=self.version).order_by("pk"))
        self.assertEqual(len(self.selected), 180)
        now = timezone.now()
        ResearchSessionMaterialization.objects.bulk_create([
            ResearchSessionMaterialization(
                session=session, source_revision=session.research_source_revision,
                status="succeeded", is_current=True, projection_version=PROJECTION_VERSION,
                activated_revision=1, activated_at=now, finished_at=now,
                session_summary={"input_completeness": {"status": "complete"}},
                session_row=[f"session-{session.pk}"],
                row_counts={"responses": 0, "visits": 0, "behavior": 0},
            ) for session in self.selected
        ])
        history = Experiment.objects.create(
            course=self.course, created_by=self.teacher,
            question_bank=self.experiment.question_bank, name="Outside selected scope",
        )
        self.history_version = ExperimentVersion.objects.create(
            experiment=history, version_number=1, published_by=self.teacher,
        )
        self.history_snapshot = ExperimentClassSnapshot.objects.create(
            version=self.history_version, source_class_group=self.class_group,
            name_snapshot=self.class_group.name,
        )

    def _insert_sessions(self, count, version, snapshot, *, prefix):
        if count == 0:
            return
        users = User.objects.bulk_create([
            User(username=f"scale-{prefix}-{index}", role=User.Role.STUDENT)
            for index in range(count)
        ], batch_size=1000)
        now = timezone.now()
        ExperimentSession.objects.bulk_create([
            ExperimentSession(
                version=version, student=user, class_snapshot=snapshot,
                class_snapshot_name=snapshot.display_name, status="completed",
                started_at=now, completed_at=now, terminal_at=now,
            ) for user in users
        ], batch_size=500)

    def test_selected_180_cost_is_stable_with_180_1800_18000_course_sessions(self):
        measurements = []
        previous_total = 180
        reference = DataScope._session_reference(self.session.pk, self.session.started_at)
        for total in (180, 1800, 18000):
            with self.subTest(course_sessions=total):
                self._insert_sessions(total - previous_total, self.history_version, self.history_snapshot, prefix=f"history-{total}")
                previous_total = total
                self.assertEqual(ExperimentSession.objects.filter(version__experiment__course=self.course).count(), total)

                with patch.object(DataScope, "session_references", side_effect=AssertionError("enumerated whole course")):
                    with patch.object(DataScope, "_session_reference", wraps=DataScope._session_reference) as references:
                        with CaptureQueriesContext(connection) as reference_queries:
                            selected_scope = DataScope.from_mapping(self.course, {**self.params, "session_ref": reference})
                            selected_ids = list(selected_scope.sessions().values_list("pk", flat=True))
                        self.assertEqual(selected_ids, [self.session.pk])
                        self.assertEqual(references.call_count, 1)
                    with patch.object(DataScope, "_session_reference", wraps=DataScope._session_reference) as references:
                        search_scope = DataScope.from_mapping(self.course, {**self.params, "search": reference.lower()})
                        self.assertEqual(list(search_scope.sessions().values_list("pk", flat=True)), [self.session.pk])
                        self.assertEqual(references.call_count, 180)
                self.assertLessEqual(len(reference_queries), 4)

                scope = DataScope.from_mapping(self.course, self.params)
                with CaptureQueriesContext(connection) as overview_queries:
                    overview = materialized_advanced_payload(self.course, scope, {"table": "sessions"})
                self.assertEqual(overview["preview"]["page"]["total_items"], 180)
                self.assertLessEqual(len(overview_queries), 8)
                with CaptureQueriesContext(connection) as status_queries:
                    status = materialized_advanced_status(scope, {})
                self.assertEqual(status["preparation"]["ready_count"], 180)
                self.assertEqual(len(status_queries), 2)
                status_sql = "\n".join(query["sql"] for query in status_queries)
                for table in ("analytics_researchsessionbehavior", "analytics_researchsessionresponse", "analytics_researchsessionvisit"):
                    self.assertNotIn(table, status_sql)

                with CaptureQueriesContext(connection) as api_overview_queries:
                    overview_response = self.client.get(f"/api/v1/teacher/courses/{self.course.pk}/data/advanced/", self.params)
                self.assertEqual(overview_response.status_code, 200)
                self.assertEqual(domain_payload(overview_response)["preview"]["page"]["total_items"], 180)
                with CaptureQueriesContext(connection) as api_status_queries:
                    status_response = self.client.get(f"/api/v1/teacher/courses/{self.course.pk}/data/advanced/status/", self.params)
                self.assertEqual(status_response.status_code, 200)
                measurements.append({
                    "course_sessions": total, "selected_sessions": 180,
                    "session_ref_with_scope": len(reference_queries),
                    "overview_core": len(overview_queries), "status_core": len(status_queries),
                    "overview_with_auth_scope": len(api_overview_queries),
                    "status_with_auth_scope": len(api_status_queries),
                })
        for key in ("session_ref_with_scope", "overview_core", "status_core", "overview_with_auth_scope", "status_with_auth_scope"):
            self.assertEqual(len({item[key] for item in measurements}), 1, measurements)
        print("teacher_read_scaling=" + json.dumps(measurements, sort_keys=True))
