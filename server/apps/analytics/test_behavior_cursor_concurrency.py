from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless

from django.core import signing
from django.db import close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.api.test_support import domain_payload
from apps.accounts.models import User
from apps.analytics import test_teacher_data_center_api as fixtures
from apps.assessment.models import ExperimentSession

from .advanced_materialized import _fallback_session_summary, _scope_signature
from .data_scope import DataScope
from .models import ResearchSessionMaterialization
from .research_materialization import PROJECTION_VERSION, _store_success


@skipUnless(connection.vendor == "postgresql", "PostgreSQL publication concurrency")
@override_settings(
    ADVANCED_DATA_MATERIALIZED_ENABLED=True,
    ADVANCED_DATA_MATERIALIZED_COURSE_IDS=frozenset({"*"}),
)
class BehaviorCursorConcurrencyTests(TransactionTestCase):
    def setUp(self):
        fixtures.TeacherDataCenterApiTests.setUp(self)
        self.session = self.attempt.session

    def _params(self, **extra):
        return {
            "experiment_id": self.experiment.pk,
            "class_id": self.class_group.pk,
            "table": "behavior_events",
            **extra,
        }

    def _get(self, **extra):
        return self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/data/advanced/",
            self._params(**extra),
        )

    def _publish(self, label="old", count=45, now=None, session=None):
        now = now or timezone.now()
        session = ExperimentSession.objects.get(pk=(session or self.session).pk)
        item = ResearchSessionMaterialization.objects.create(
            session=session, source_revision=session.research_source_revision,
            status="running", locked_by="cursor-worker",
            lease_expires_at=timezone.now() + timedelta(minutes=10),
        )
        summary = _fallback_session_summary(session)
        return _store_success(
            item.pk, worker_id="cursor-worker", now=now, cutoff=now,
            watermarks={"event": 0, "grade": {}},
            bundle={
                "sessions": [summary], "tables": {"sessions": ([], [[]])},
                "behavior": [
                    {"sequence": index, "row": [f"{label}-{index}"]}
                    for index in range(1, count + 1)
                ],
            },
        )

    def _publish_on_connection(self, *args, **kwargs):
        close_old_connections()
        try:
            return self._publish(*args, **kwargs).pk
        finally:
            connection.close()

    def test_v1_cursor_is_explicitly_rejected_before_interpretation(self):
        self._publish()
        scope = DataScope.from_mapping(self.course, self._params())
        cursor = signing.dumps({
            "scope": _scope_signature(scope, self._params()),
            "cutoff": timezone.now().isoformat(), "direction": "next",
            "session_id": self.session.pk, "ordinal": 20, "page": 2,
        }, salt="analytics.advanced-behavior.v1")
        response = self._get(cursor=cursor)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "BEHAVIOR_CURSOR_VERSION_CHANGED")

    def test_slow_count_does_not_hold_activation_lock_on_actual_api_path(self):
        self._publish()
        published = []
        with ThreadPoolExecutor(max_workers=1) as pool:
            def execute(execute, sql, params, many, context):
                if "COUNT(" in sql and '"analytics_researchsessionbehavior"' in sql and not published:
                    # Publishing must finish while this read is still delayed.
                    published.append(pool.submit(self._publish_on_connection, "new"))
                    try:
                        published[0].result(timeout=4)
                    except TimeoutError:
                        self.fail("Behavior COUNT kept the publication activation lock")
                    self.assertFalse(connection.in_atomic_block)
                return execute(sql, params, many, context)

            with connection.execute_wrapper(execute):
                response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(domain_payload(response)["preview"]["rows"], [[f"old-{i}"] for i in range(1, 21)])
        self.assertEqual(len(published), 1)

    def test_fixed_cursor_survives_publication_and_reverse_page(self):
        self._publish()
        first = domain_payload(self._get())["preview"]
        self._publish("new", 3)
        second = domain_payload(self._get(cursor=first["cursor"]["next"]))["preview"]
        third = domain_payload(self._get(cursor=second["cursor"]["next"]))["preview"]
        self.assertEqual(first["rows"] + second["rows"] + third["rows"], [[f"old-{i}"] for i in range(1, 46)])
        previous = domain_payload(self._get(cursor=second["cursor"]["previous"]))["preview"]
        self.assertEqual(previous["rows"], first["rows"])
        self.assertEqual(third["cursor"]["result_version"], first["cursor"]["result_version"])
        self.assertEqual(domain_payload(self._get())["preview"]["rows"], [[f"new-{i}"] for i in range(1, 4)])

    def _another_session(self):
        student = User.objects.create_user(username="cursor-phantom", role=User.Role.STUDENT)
        return ExperimentSession.objects.create(
            version=self.version, student=student, class_snapshot=self.class_snapshot,
            class_snapshot_name=self.class_snapshot.display_name,
            status=ExperimentSession.Status.COMPLETED,
        )

    def test_session_published_after_cutoff_cannot_enter_existing_cursor(self):
        self._publish()
        first = domain_payload(self._get())["preview"]
        self._publish("phantom", 2, session=self._another_session())
        second = domain_payload(self._get(cursor=first["cursor"]["next"]))["preview"]
        third = domain_payload(self._get(cursor=second["cursor"]["next"]))["preview"]
        self.assertEqual(third["rows"], [[f"old-{i}"] for i in range(41, 46)])
        self.assertEqual(third["page"]["total_items"], 45)
        self.assertIsNone(third["cursor"]["next"])
        self.assertEqual(domain_payload(self._get())["preview"]["page"]["total_items"], 47)

    def test_two_connections_publish_distinct_monotonic_revisions(self):
        other = self._another_session()
        barrier = Barrier(2)

        def publish(session):
            barrier.wait(timeout=5)
            return self._publish_on_connection(session=session)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(publish, session) for session in (self.session, other)]
            ids = [future.result(timeout=10) for future in futures]
        revisions = list(ResearchSessionMaterialization.objects.filter(pk__in=ids).values_list("activated_revision", flat=True))
        self.assertEqual(len(set(revisions)), 2)
        self.assertEqual(max(revisions) - min(revisions), 1)

    def test_same_time_and_clock_rollback_do_not_change_cursor_boundary(self):
        from .models import ResearchPublicationClock

        now = timezone.now()
        old = self._publish(now=now)
        first = domain_payload(self._get())["preview"]
        same = self._publish("same", now=now)
        back = self._publish("back", now=now - timedelta(seconds=10))
        self.assertLess(old.activated_revision, same.activated_revision)
        self.assertLess(same.activated_revision, back.activated_revision)
        self.assertEqual(ResearchPublicationClock.objects.get(pk=1).revision, back.activated_revision)
        second = domain_payload(self._get(cursor=first["cursor"]["next"]))["preview"]
        self.assertEqual(second["rows"], [[f"old-{i}"] for i in range(21, 41)])

    def test_publish_rollback_does_not_advance_clock_or_supersede_result(self):
        from .models import ResearchPublicationClock

        old = self._publish()
        revision = ResearchPublicationClock.objects.get(pk=1).revision
        with self.assertRaisesMessage(RuntimeError, "rollback"):
            with transaction.atomic():
                self._publish("rollback")
                raise RuntimeError("rollback")
        old.refresh_from_db()
        self.assertTrue(old.is_current)
        self.assertIsNone(old.superseded_revision)
        self.assertEqual(ResearchPublicationClock.objects.get(pk=1).revision, revision)

    def test_first_page_refuses_outer_transaction_that_would_extend_lock(self):
        from .advanced_materialized import _behavior_page

        self._publish()
        scope = DataScope.from_mapping(self.course, self._params())
        with transaction.atomic(), self.assertRaisesMessage(RuntimeError, "durable"):
            _behavior_page(scope, ExperimentSession.objects.filter(pk=self.session.pk), self._params())

    def test_cursor_scope_contains_course_and_rejects_tampering(self):
        from dataclasses import replace
        from rest_framework.exceptions import ValidationError
        from .advanced_materialized import _decode_cursor

        scope = DataScope.from_mapping(self.course, self._params())
        other_course = type(self.course).objects.create(owner=self.teacher, name="Other", code="OTHER")
        self.assertNotEqual(_scope_signature(scope, self._params()), _scope_signature(replace(scope, course=other_course), self._params()))
        self._publish()
        cursor = domain_payload(self._get())["preview"]["cursor"]["next"]
        with self.assertRaisesMessage(ValidationError, "当前数据范围不一致"):
            _decode_cursor(cursor, signature=_scope_signature(replace(scope, course=other_course), self._params()))
        self.assertEqual(self._get(cursor=cursor + "tamper").status_code, 400)

    def test_source_revision_mismatch_cannot_publish_or_advance_clock(self):
        from .models import ResearchPublicationClock
        from .research_materialization import SourceChanged

        old = self._publish()
        item = ResearchSessionMaterialization.objects.create(
            session=self.session, status="running", locked_by="cursor-worker",
            source_revision=old.source_revision,
            lease_expires_at=timezone.now() + timedelta(minutes=5),
        )
        ExperimentSession.objects.filter(pk=self.session.pk).update(research_source_revision=old.source_revision + 1)
        with self.assertRaises(SourceChanged):
            _store_success(item.pk, worker_id="cursor-worker", bundle={}, cutoff=timezone.now(), watermarks={})
        self.assertEqual(ResearchPublicationClock.objects.get(pk=1).revision, old.activated_revision)


@skipUnless(connection.vendor == "postgresql", "PostgreSQL publication migration")
class PublicationSequenceMigrationTests(TransactionTestCase):
    old_target = [("analytics", "0023_comparison_source_signature")]
    new_target = [("analytics", "0024_research_publication_sequence")]

    def test_empty_database_initializes_clock_without_materializations(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate(self.old_target)
            executor = MigrationExecutor(connection)
            executor.migrate(self.new_target)
            apps = executor.loader.project_state(self.new_target).apps
            self.assertEqual(apps.get_model("analytics", "ResearchPublicationClock").objects.get(pk=1).revision, 1)
            self.assertFalse(apps.get_model("analytics", "ResearchSessionMaterialization").objects.exists())
        finally:
            MigrationExecutor(connection).migrate(latest)

    def test_history_upgrade_preserves_content_and_time_without_inventing_order(self):
        fixtures.TeacherDataCenterApiTests.setUp(self)
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate(self.old_target)
            historical_apps = executor.loader.project_state(self.old_target).apps
            materializations = historical_apps.get_model("analytics", "ResearchSessionMaterialization")
            when = timezone.now() - timedelta(days=2)
            current = materializations.objects.create(
                session_id=self.session.pk, status="succeeded", is_current=True,
                activated_at=when, finished_at=when, source_revision=17,
                session_row=["preserve-current"], quality_rows=[{"rule": "keep"}],
            )
            history = materializations.objects.create(
                session_id=self.session.pk, status="succeeded", is_current=False,
                activated_at=when - timedelta(days=1), superseded_at=when,
                session_row=["preserve-history"],
            )
            queued = materializations.objects.create(session_id=self.session.pk)
            executor = MigrationExecutor(connection)
            executor.migrate(self.new_target)
            new_apps = executor.loader.project_state(self.new_target).apps
            upgraded = new_apps.get_model("analytics", "ResearchSessionMaterialization")
            current = upgraded.objects.get(pk=current.pk)
            history = upgraded.objects.get(pk=history.pk)
            queued = upgraded.objects.get(pk=queued.pk)
            self.assertEqual((current.activated_revision, current.superseded_revision), (1, None))
            self.assertEqual((history.activated_revision, history.superseded_revision), (0, 1))
            self.assertEqual((queued.activated_revision, queued.superseded_revision), (None, None))
            self.assertEqual((current.activated_at, current.finished_at, current.source_revision), (when, when, 17))
            self.assertEqual(current.session_row, ["preserve-current"])
            self.assertEqual(current.quality_rows, [{"rule": "keep"}])
            self.assertEqual(history.superseded_at, when)
            self.assertEqual(history.session_row, ["preserve-history"])
        finally:
            MigrationExecutor(connection).migrate(latest)
