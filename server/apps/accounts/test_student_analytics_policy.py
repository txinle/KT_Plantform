from apps.api.test_support import domain_payload
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.contrib import admin
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, connections
from django.db.models.deletion import ProtectedError
from django.db.migrations.executor import MigrationExecutor
from django.db.models import QuerySet
from django.db.models.signals import post_save
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.experiments.models import (
    ClassExperimentTask,
    Experiment,
    ExperimentClassSnapshot,
    ExperimentVersion,
)
from apps.question_bank.models import QuestionBank

from .models import (
    ClassGroup,
    ClassStudentAnalyticsPolicy,
    Course,
    CourseTeacher,
    User,
)
from .student_analytics_policy import (
    POLICY_FIELD_NAMES,
    update_class_student_analytics_policy,
)


class ClassStudentAnalyticsPolicyModelTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="policy-model-owner",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            name="策略模型课程",
            code="POLMODEL",
            term="2026秋",
            owner=self.owner,
        )

    def test_new_class_group_immediately_receives_the_default_policy(self):
        class_group = ClassGroup.objects.create(
            course=self.course,
            owner=self.owner,
            name="策略模型一班",
            academic_year="2026",
        )

        policy = class_group.student_analytics_policy

        self.assertEqual(policy.class_group_id, class_group.pk)
        self.assertTrue(policy.show_scores)
        self.assertTrue(policy.show_correct_rate)
        self.assertTrue(policy.show_item_results)
        self.assertTrue(policy.show_class_average)
        self.assertEqual(policy.rank_mode, ClassStudentAnalyticsPolicy.RankMode.SELF)
        self.assertTrue(policy.show_kc_rates)
        self.assertTrue(policy.show_trend)
        self.assertIsNone(policy.updated_by_id)
        self.assertIsNotNone(policy.updated_at)

    def test_policy_is_one_to_one_with_the_class_group(self):
        class_group = ClassGroup.objects.create(
            course=self.course,
            owner=self.owner,
            name="策略模型二班",
            academic_year="2026",
        )

        self.assertEqual(
            ClassStudentAnalyticsPolicy.objects.filter(class_group=class_group).count(),
            1,
        )


class ClassStudentAnalyticsPolicyBulkCreateTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="policy-bulk-owner",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            name="策略批量课程",
            code="POLBULK",
            term="2026秋",
            owner=self.owner,
        )

    def test_normal_bulk_create_immediately_creates_default_policies(self):
        class_groups = ClassGroup.objects.bulk_create(
            [
                ClassGroup(
                    course=self.course,
                    owner=self.owner,
                    name="策略批量一班",
                    academic_year="2026",
                ),
                ClassGroup(
                    course=self.course,
                    owner=self.owner,
                    name="策略批量二班",
                    academic_year="2026",
                ),
            ]
        )

        self.assertTrue(all(class_group.pk for class_group in class_groups))
        self.assertEqual(
            ClassStudentAnalyticsPolicy.objects.filter(
                class_group_id__in=[class_group.pk for class_group in class_groups]
            ).count(),
            2,
        )

    def test_policy_creation_failure_rolls_back_bulk_created_classes(self):
        policy_manager = SimpleNamespace(
            bulk_create=Mock(side_effect=RuntimeError("policy insert failed"))
        )

        with patch.object(
            ClassStudentAnalyticsPolicy.objects,
            "using",
            return_value=policy_manager,
        ):
            with self.assertRaises(RuntimeError):
                ClassGroup.objects.bulk_create(
                    [
                        ClassGroup(
                            course=self.course,
                            owner=self.owner,
                            name="策略回滚班",
                            academic_year="2026",
                        )
                    ]
                )

        self.assertFalse(
            ClassGroup.objects.filter(course=self.course, name="策略回滚班").exists()
        )
        self.assertEqual(
            ClassStudentAnalyticsPolicy.objects.filter(
                class_group__course=self.course,
                class_group__name="策略回滚班",
            ).count(),
            0,
        )

    def test_existing_non_course_upsert_never_duplicates_its_policy(self):
        class_group = ClassGroup.objects.create(
            course=self.course,
            owner=self.owner,
            name="策略上突班",
            academic_year="2026",
        )
        candidate = ClassGroup.objects.get(pk=class_group.pk)
        candidate.is_active = False

        ClassGroup.objects.bulk_create(
            [candidate],
            update_conflicts=True,
            update_fields=["is_active"],
            unique_fields=["pk"],
        )

        class_group.refresh_from_db()
        self.assertFalse(class_group.is_active)
        self.assertEqual(
            ClassStudentAnalyticsPolicy.objects.filter(class_group=class_group).count(),
            1,
        )

    def test_ignore_conflicts_is_rejected_without_creating_a_class_or_policy(self):
        with self.assertRaises(ValidationError):
            ClassGroup.objects.bulk_create(
                [
                    ClassGroup(
                        course=self.course,
                        owner=self.owner,
                        name="策略忽略冲突班",
                        academic_year="2026",
                    )
                ],
                ignore_conflicts=True,
            )

        self.assertFalse(
            ClassGroup.objects.filter(course=self.course, name="策略忽略冲突班").exists()
        )
        self.assertEqual(
            ClassStudentAnalyticsPolicy.objects.filter(
                class_group__course=self.course,
                class_group__name="策略忽略冲突班",
            ).count(),
            0,
        )


class ClassStudentAnalyticsPolicySaveTransactionTests(TransactionTestCase):
    def test_new_class_save_rolls_back_when_default_policy_creation_fails(self):
        owner = User.objects.create_user(
            username="policy-save-transaction-owner",
            role=User.Role.TEACHER,
        )
        course = Course.objects.create(
            name="策略普通保存事务课程",
            code="POLSAVE",
            term="2026秋",
            owner=owner,
        )
        policy_manager = SimpleNamespace(
            get_or_create=Mock(side_effect=IntegrityError("policy insert failed"))
        )

        with patch.object(
            ClassStudentAnalyticsPolicy.objects,
            "using",
            return_value=policy_manager,
        ):
            with self.assertRaises(IntegrityError):
                ClassGroup.objects.create(
                    course=course,
                    owner=owner,
                    name="策略普通保存回滚班",
                    academic_year="2026",
                )

        self.assertFalse(
            ClassGroup.objects.filter(
                course=course,
                name="策略普通保存回滚班",
            ).exists()
        )
        self.assertFalse(
            ClassStudentAnalyticsPolicy.objects.filter(
                class_group__course=course,
                class_group__name="策略普通保存回滚班",
            ).exists()
        )


class ClassStudentAnalyticsPolicyMigrationTests(TransactionTestCase):
    reset_sequences = True
    migrate_from = ("accounts", "0008_alter_classgroup_options")
    migrate_to = ("accounts", "0009_class_student_analytics_policy")

    @staticmethod
    def _targets_at_accounts_migration(executor, accounts_target):
        graph = executor.loader.graph
        required = set(graph.forwards_plan(accounts_target))
        blocked = set()
        for key in graph.nodes:
            if key[0] == "accounts" and key not in required:
                blocked.update(graph.backwards_plan(key))
        # Keep each app at its latest dependency-consistent migration. Retaining
        # current dependent leaves would immediately reapply the account schema.
        remaining = set(graph.nodes) - blocked
        targets = [key for key in remaining
                   if not any(child.key in remaining and child.key[0] == key[0]
                              for child in graph.node_map[key].children)]
        remaining_apps = {key[0] for key in remaining}
        targets.extend((app, None) for app in executor.loader.migrated_apps - remaining_apps)
        return sorted(targets)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.executor = MigrationExecutor(connection)
        cls.current_targets = cls.executor.loader.graph.leaf_nodes()
        cls.old_targets = cls._targets_at_accounts_migration(cls.executor, cls.migrate_from)
        cls.executor.migrate(cls.old_targets)
        old_apps = cls.executor.loader.project_state(cls.old_targets).apps
        User = old_apps.get_model("accounts", "User")
        Course = old_apps.get_model("accounts", "Course")
        ClassGroup = old_apps.get_model("accounts", "ClassGroup")

        teacher = User.objects.create(username="policy-migration-owner", role="teacher")
        course = Course.objects.create(
            name="策略迁移课程",
            code="POLMIG",
            term="2026秋",
            owner_id=teacher.pk,
        )
        class_group = ClassGroup.objects.create(
            course_id=course.pk,
            owner_id=teacher.pk,
            name="迁移前班级",
            academic_year="2026",
        )
        cls.class_group_pk = class_group.pk
        cls.course_pk = course.pk

        cls.executor = MigrationExecutor(connection)
        cls.new_targets = cls._targets_at_accounts_migration(cls.executor, cls.migrate_to)
        cls.executor.migrate(cls.new_targets)
        cls.apps = cls.executor.loader.project_state(cls.new_targets).apps

    @classmethod
    def tearDownClass(cls):
        try:
            MigrationExecutor(connection).migrate(cls.current_targets)
        finally:
            super().tearDownClass()

    def test_forward_backfill_has_defaults_and_reverse_preserves_unrelated_rows(self):
        Policy = self.apps.get_model("accounts", "ClassStudentAnalyticsPolicy")
        policy = Policy.objects.get(class_group_id=self.class_group_pk)

        self.assertTrue(policy.show_scores)
        self.assertTrue(policy.show_correct_rate)
        self.assertTrue(policy.show_item_results)
        self.assertTrue(policy.show_class_average)
        self.assertEqual(policy.rank_mode, "self")
        self.assertTrue(policy.show_kc_rates)
        self.assertTrue(policy.show_trend)
        self.assertIsNone(policy.updated_by_id)
        self.assertIsNotNone(policy.updated_at)

        MigrationExecutor(connection).migrate(self.old_targets)
        reversed_apps = MigrationExecutor(connection).loader.project_state(self.old_targets).apps
        ReversedClassGroup = reversed_apps.get_model("accounts", "ClassGroup")
        reversed_class_group = ReversedClassGroup.objects.get(pk=self.class_group_pk)
        self.assertEqual(reversed_class_group.course_id, self.course_pk)
        self.assertEqual(reversed_class_group.name, "迁移前班级")
        self.assertEqual(reversed_class_group.academic_year, "2026")
        with self.assertRaises(LookupError):
            reversed_apps.get_model("accounts", "ClassStudentAnalyticsPolicy")

        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.new_targets)
        self.apps = self.executor.loader.project_state(self.new_targets).apps


class ClassStudentAnalyticsPolicyFixture:
    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user(
            username="policy-owner",
            role=User.Role.TEACHER,
        )
        self.collaborator = User.objects.create_user(
            username="policy-collaborator",
            role=User.Role.TEACHER,
        )
        self.other_teacher = User.objects.create_user(
            username="policy-other-teacher",
            role=User.Role.TEACHER,
        )
        self.student = User.objects.create_user(
            username="policy-student",
            student_number="policy-student",
            role=User.Role.STUDENT,
        )
        self.role_admin = User.objects.create_user(
            username="policy-role-admin",
            role=User.Role.ADMIN,
        )
        self.superuser = User.objects.create_superuser(
            username="policy-superuser",
            password="password123",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            name="策略服务课程",
            code="POLSVC",
            term="2026秋",
            owner=self.owner,
        )
        self.course.teacher_links.create(teacher=self.collaborator)
        self.class_group = ClassGroup.objects.create(
            course=self.course,
            owner=self.owner,
            name="策略服务一班",
            academic_year="2026",
        )
        self.policy = self.class_group.student_analytics_policy

    def policy_state(self, policy=None):
        policy = policy or self.policy
        return {field: getattr(policy, field) for field in POLICY_FIELD_NAMES}


class ClassStudentAnalyticsPolicyServiceTests(
    ClassStudentAnalyticsPolicyFixture,
    TestCase,
):
    def test_disabling_correct_rate_hides_kc_rates_and_trend(self):
        policy = update_class_student_analytics_policy(
            class_group=self.class_group,
            actor=self.owner,
            changes={"show_correct_rate": False},
        )

        self.assertFalse(policy.show_correct_rate)
        self.assertFalse(policy.show_kc_rates)
        self.assertFalse(policy.show_trend)
        self.assertEqual(
            AuditLog.objects.get().details["changed_fields"],
            ["show_correct_rate", "show_kc_rates", "show_trend"],
        )

    def test_disabling_scores_hides_rank_mode(self):
        policy = update_class_student_analytics_policy(
            class_group=self.class_group,
            actor=self.owner,
            changes={"show_scores": False},
        )

        self.assertFalse(policy.show_scores)
        self.assertEqual(policy.rank_mode, ClassStudentAnalyticsPolicy.RankMode.NONE)
        self.assertEqual(
            AuditLog.objects.get().details["changed_fields"],
            ["show_scores", "rank_mode"],
        )

    def test_reopening_parent_fields_does_not_restore_downstream_values(self):
        update_class_student_analytics_policy(
            class_group=self.class_group,
            actor=self.owner,
            changes={"show_correct_rate": False, "show_scores": False},
        )
        policy = update_class_student_analytics_policy(
            class_group=self.class_group,
            actor=self.owner,
            changes={"show_correct_rate": True, "show_scores": True},
        )

        self.assertTrue(policy.show_correct_rate)
        self.assertTrue(policy.show_scores)
        self.assertFalse(policy.show_kc_rates)
        self.assertFalse(policy.show_trend)
        self.assertEqual(policy.rank_mode, ClassStudentAnalyticsPolicy.RankMode.NONE)

    def test_explicit_downstream_values_are_required_to_restore_them(self):
        update_class_student_analytics_policy(
            class_group=self.class_group,
            actor=self.owner,
            changes={"show_correct_rate": False, "show_scores": False},
        )
        update_class_student_analytics_policy(
            class_group=self.class_group,
            actor=self.owner,
            changes={"show_correct_rate": True, "show_scores": True},
        )

        policy = update_class_student_analytics_policy(
            class_group=self.class_group,
            actor=self.owner,
            changes={
                "show_kc_rates": True,
                "show_trend": True,
                "rank_mode": ClassStudentAnalyticsPolicy.RankMode.SELF,
            },
        )

        self.assertTrue(policy.show_kc_rates)
        self.assertTrue(policy.show_trend)
        self.assertEqual(policy.rank_mode, ClassStudentAnalyticsPolicy.RankMode.SELF)

    def test_owner_collaborator_role_admin_and_superuser_can_update(self):
        for actor in (
            self.owner,
            self.collaborator,
            self.role_admin,
            self.superuser,
        ):
            with self.subTest(actor=actor.username):
                self.policy.refresh_from_db()
                policy = update_class_student_analytics_policy(
                    class_group=self.class_group,
                    actor=actor,
                    changes={"show_item_results": not self.policy.show_item_results},
                )
                self.assertEqual(policy.updated_by_id, actor.pk)

        self.assertEqual(AuditLog.objects.count(), 4)

    def test_other_teacher_student_and_anonymous_actor_are_rejected(self):
        before = self.policy_state()
        for actor in (self.other_teacher, self.student, AnonymousUser()):
            with self.subTest(actor=getattr(actor, "username", "anonymous")):
                with self.assertRaises(PermissionDenied):
                    update_class_student_analytics_policy(
                        class_group=self.class_group,
                        actor=actor,
                        changes={"show_item_results": False},
                    )

        self.policy.refresh_from_db()
        self.assertEqual(self.policy_state(), before)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_stale_admin_is_rejected_after_its_persisted_role_is_revoked(self):
        stale_admin = User.objects.get(pk=self.role_admin.pk)
        User.objects.filter(pk=self.role_admin.pk).update(role=User.Role.STUDENT)

        with self.assertRaises(PermissionDenied):
            update_class_student_analytics_policy(
                class_group=self.class_group,
                actor=stale_admin,
                changes={"show_item_results": False},
            )

        self.policy.refresh_from_db()
        self.assertTrue(self.policy.show_item_results)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_stale_collaborator_is_rejected_after_role_or_active_drift(self):
        stale_collaborator = User.objects.get(pk=self.collaborator.pk)
        User.objects.filter(pk=self.collaborator.pk).update(role=User.Role.STUDENT)

        with self.assertRaises(PermissionDenied):
            update_class_student_analytics_policy(
                class_group=self.class_group,
                actor=stale_collaborator,
                changes={"show_item_results": False},
            )

        User.objects.filter(pk=self.collaborator.pk).update(
            role=User.Role.TEACHER,
            is_active=False,
        )
        stale_collaborator = User.objects.get(pk=self.collaborator.pk)
        with self.assertRaises(PermissionDenied):
            update_class_student_analytics_policy(
                class_group=self.class_group,
                actor=stale_collaborator,
                changes={"show_item_results": False},
            )

        self.policy.refresh_from_db()
        self.assertTrue(self.policy.show_item_results)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_unsaved_forged_admin_actor_is_rejected_without_writes(self):
        forged_actor = User(pk=self.student.pk, role=User.Role.ADMIN)

        with self.assertRaises(PermissionDenied):
            update_class_student_analytics_policy(
                class_group=self.class_group,
                actor=forged_actor,
                changes={"show_item_results": False},
            )

        self.policy.refresh_from_db()
        self.assertTrue(self.policy.show_item_results)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_cross_database_actor_state_is_rejected_without_writes(self):
        cross_database_actor = User.objects.get(pk=self.owner.pk)
        cross_database_actor._state.db = "other"

        with self.assertRaises(PermissionDenied):
            update_class_student_analytics_policy(
                class_group=self.class_group,
                actor=cross_database_actor,
                changes={"show_item_results": False},
            )

        self.policy.refresh_from_db()
        self.assertTrue(self.policy.show_item_results)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_class_group_alias_mismatch_is_rejected_without_writes(self):
        with patch("django.db.router.db_for_write", return_value="other"):
            with self.assertRaises(ValidationError):
                update_class_student_analytics_policy(
                    class_group=self.class_group,
                    actor=self.owner,
                    changes={"show_item_results": False},
                )

        self.policy.refresh_from_db()
        self.assertTrue(self.policy.show_item_results)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_unsaved_forged_class_group_is_rejected_without_writes(self):
        forged_class_group = ClassGroup(pk=self.class_group.pk)

        with self.assertRaises(ValidationError):
            update_class_student_analytics_policy(
                class_group=forged_class_group,
                actor=self.owner,
                changes={"show_item_results": False},
            )

        self.policy.refresh_from_db()
        self.assertTrue(self.policy.show_item_results)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_archived_course_is_rejected_after_locked_reload(self):
        Course.objects.filter(pk=self.course.pk).update(status=Course.Status.ARCHIVED)

        with self.assertRaises(PermissionDenied):
            update_class_student_analytics_policy(
                class_group=self.class_group,
                actor=self.owner,
                changes={"show_item_results": False},
            )

        self.assertEqual(AuditLog.objects.count(), 0)

    def test_legacy_class_without_course_is_rejected(self):
        legacy_class_group = ClassGroup.objects.create(
            owner=self.owner,
            name="遗留策略班级",
            academic_year="2026",
        )

        with self.assertRaises(ValidationError):
            update_class_student_analytics_policy(
                class_group=legacy_class_group,
                actor=self.owner,
                changes={"show_item_results": False},
            )

        self.assertTrue(
            ClassStudentAnalyticsPolicy.objects.filter(class_group=legacy_class_group).exists()
        )
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_unknown_or_invalid_values_make_no_write_and_no_audit(self):
        before = self.policy_state()
        before_updated_at = self.policy.updated_at
        invalid_changes = (
            {"unknown": True},
            {"show_scores": 1},
            {"show_correct_rate": "false"},
            {"rank_mode": "full"},
            {"rank_mode": True},
        )

        for changes in invalid_changes:
            with self.subTest(changes=changes):
                with self.assertRaises(ValidationError):
                    update_class_student_analytics_policy(
                        class_group=self.class_group,
                        actor=self.owner,
                        changes=changes,
                    )

        self.policy.refresh_from_db()
        self.assertEqual(self.policy_state(), before)
        self.assertEqual(self.policy.updated_at, before_updated_at)
        self.assertIsNone(self.policy.updated_by_id)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_audit_uses_only_the_frozen_policy_fields(self):
        policy = update_class_student_analytics_policy(
            class_group=self.class_group,
            actor=self.owner,
            changes={"show_item_results": False},
        )
        audit = AuditLog.objects.get()

        self.assertEqual(audit.action, "class.student_analytics_policy.update")
        self.assertEqual(audit.actor_id, self.owner.pk)
        self.assertEqual(audit.object_type, "ClassStudentAnalyticsPolicy")
        self.assertEqual(audit.object_id, str(policy.pk))
        self.assertIsNone(audit.ip_address)
        self.assertEqual(
            set(audit.details),
            {"course_id", "class_group_id", "policy_id", "before", "after", "changed_fields"},
        )
        self.assertEqual(audit.details["course_id"], self.course.pk)
        self.assertEqual(audit.details["class_group_id"], self.class_group.pk)
        self.assertEqual(audit.details["policy_id"], policy.pk)
        self.assertEqual(audit.details["changed_fields"], ["show_item_results"])
        self.assertEqual(set(audit.details["before"]), set(POLICY_FIELD_NAMES))
        self.assertEqual(set(audit.details["after"]), set(POLICY_FIELD_NAMES))
        self.assertTrue(audit.details["before"]["show_item_results"])
        self.assertFalse(audit.details["after"]["show_item_results"])

    def test_no_op_does_not_touch_policy_or_create_audit(self):
        before_updated_at = self.policy.updated_at

        policy = update_class_student_analytics_policy(
            class_group=self.class_group,
            actor=self.owner,
            changes={"show_scores": True},
        )

        self.assertEqual(policy.updated_at, before_updated_at)
        self.assertIsNone(policy.updated_by_id)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_stale_class_group_cannot_bypass_current_course_permission(self):
        stale_class_group = ClassGroup.objects.get(pk=self.class_group.pk)
        other_course = Course.objects.create(
            name="其他教师课程",
            code="POLSTALE",
            term="2026秋",
            owner=self.other_teacher,
        )
        ClassGroup.objects.filter(pk=self.class_group.pk).update(course=other_course)

        with self.assertRaises(PermissionDenied):
            update_class_student_analytics_policy(
                class_group=stale_class_group,
                actor=self.owner,
                changes={"show_item_results": False},
            )

        self.assertEqual(AuditLog.objects.count(), 0)

    def test_plain_policy_read_does_not_create_an_audit_row(self):
        ClassStudentAnalyticsPolicy.objects.get(pk=self.policy.pk)

        self.assertEqual(AuditLog.objects.count(), 0)

    def test_service_uses_the_write_alias_without_read_routing(self):
        with (
            patch(
                "django.db.router.db_for_read",
                side_effect=AssertionError("policy update must not route to a reader"),
            ) as db_for_read,
            patch("django.db.router.db_for_write", return_value="default") as db_for_write,
        ):
            update_class_student_analytics_policy(
                class_group=self.class_group,
                actor=self.owner,
                changes={"show_item_results": False},
            )

        db_for_write.assert_called()
        db_for_read.assert_not_called()


class ClassStudentAnalyticsPolicyTransactionTests(
    ClassStudentAnalyticsPolicyFixture,
    TransactionTestCase,
):
    def test_update_selects_for_update_on_course_class_and_policy_inside_atomic_block(self):
        locked_models = []
        atomic_states = []
        original_select_for_update = QuerySet.select_for_update

        def capture_select_for_update(queryset, *args, **kwargs):
            locked_models.append(queryset.model)
            return original_select_for_update(queryset, *args, **kwargs)

        def capture_audit_transaction(sender, using, **kwargs):
            atomic_states.append(connections[using].in_atomic_block)

        post_save.connect(
            capture_audit_transaction,
            sender=AuditLog,
            dispatch_uid="policy-transaction-capture",
        )
        try:
            # SQLite does not provide row-level lock semantics; this asserts the
            # service issues the locking calls while its audit write is atomic.
            with patch.object(QuerySet, "select_for_update", new=capture_select_for_update):
                update_class_student_analytics_policy(
                    class_group=self.class_group,
                    actor=self.owner,
                    changes={"show_item_results": False},
                )
        finally:
            post_save.disconnect(
                capture_audit_transaction,
                sender=AuditLog,
                dispatch_uid="policy-transaction-capture",
            )

        self.assertTrue(
            {Course, ClassGroup, ClassStudentAnalyticsPolicy}.issubset(set(locked_models))
        )
        self.assertEqual(
            locked_models[:4],
            [Course, ClassGroup, ClassStudentAnalyticsPolicy, User],
        )
        self.assertEqual(atomic_states, [True])

    def test_audit_database_error_rolls_back_the_policy_change(self):
        before = self.policy_state()

        def raise_during_audit(sender, **kwargs):
            raise RuntimeError("audit persistence failed")

        post_save.connect(
            raise_during_audit,
            sender=AuditLog,
            dispatch_uid="policy-audit-write-failure",
        )
        try:
            with self.assertRaises(RuntimeError):
                update_class_student_analytics_policy(
                    class_group=self.class_group,
                    actor=self.owner,
                    changes={"show_item_results": False},
                )
        finally:
            post_save.disconnect(
                raise_during_audit,
                sender=AuditLog,
                dispatch_uid="policy-audit-write-failure",
            )

        self.policy.refresh_from_db()
        self.assertEqual(self.policy_state(), before)
        self.assertIsNone(self.policy.updated_by_id)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_collaborator_update_locks_teacher_link_after_actor(self):
        locked_models = []
        original_select_for_update = QuerySet.select_for_update

        def capture_select_for_update(queryset, *args, **kwargs):
            locked_models.append(queryset.model)
            return original_select_for_update(queryset, *args, **kwargs)

        with patch.object(QuerySet, "select_for_update", new=capture_select_for_update):
            update_class_student_analytics_policy(
                class_group=self.class_group,
                actor=self.collaborator,
                changes={"show_item_results": False},
            )

        self.assertEqual(
            locked_models[:5],
            [Course, ClassGroup, ClassStudentAnalyticsPolicy, User, CourseTeacher],
        )


class ClassStudentAnalyticsPolicyAdminTests(TestCase):
    def test_policy_admin_is_registered_and_cannot_write_directly(self):
        from .admin import ClassStudentAnalyticsPolicyAdmin

        policy_admin = admin.site._registry[ClassStudentAnalyticsPolicy]

        self.assertIsInstance(policy_admin, ClassStudentAnalyticsPolicyAdmin)
        self.assertFalse(policy_admin.has_add_permission(None))
        self.assertFalse(policy_admin.has_change_permission(None))
        self.assertFalse(policy_admin.has_delete_permission(None))
        self.assertEqual(
            set(policy_admin.get_readonly_fields(None)),
            {
                "class_group",
                *POLICY_FIELD_NAMES,
                "updated_by",
                "updated_at",
            },
        )

    def test_course_collaborator_inline_and_class_group_delete_are_disabled(self):
        from .admin import ClassGroupAdmin, CourseAdmin

        class_group_admin = admin.site._registry[ClassGroup]

        self.assertIsInstance(class_group_admin, ClassGroupAdmin)
        self.assertFalse(class_group_admin.has_delete_permission(None))
        self.assertFalse(CourseAdmin.inlines)


class ClassStudentAnalyticsPolicyDeletionIntegrationTests(TestCase):
    def test_direct_class_deletion_is_blocked_by_the_policy_fk(self):
        owner = User.objects.create_user(
            username="policy-direct-delete-owner",
            role=User.Role.TEACHER,
        )
        course = Course.objects.create(
            name="策略直接删除课程",
            code="POLDIRECT",
            term="2026秋",
            owner=owner,
        )
        class_group = ClassGroup.objects.create(
            course=course,
            owner=owner,
            name="策略直接删除一班",
            academic_year="2026",
        )

        with self.assertRaises(ProtectedError):
            class_group.delete()
        with self.assertRaises(ProtectedError):
            ClassGroup.objects.filter(pk=class_group.pk).delete()

        self.assertTrue(ClassGroup.objects.filter(pk=class_group.pk).exists())
        self.assertTrue(
            ClassStudentAnalyticsPolicy.objects.filter(class_group=class_group).exists()
        )

    def test_class_delete_removes_its_protected_policy_with_the_class(self):
        owner = User.objects.create_user(
            username="policy-delete-owner",
            role=User.Role.TEACHER,
        )
        collaborator = User.objects.create_user(
            username="policy-delete-collaborator",
            role=User.Role.TEACHER,
        )
        course = Course.objects.create(
            name="策略删除课程",
            code="POLDELETE",
            term="2026秋",
            owner=owner,
        )
        course.teacher_links.create(teacher=collaborator)
        class_group = ClassGroup.objects.create(
            course=course,
            owner=owner,
            name="策略删除一班",
            academic_year="2026",
        )
        policy_pk = class_group.student_analytics_policy.pk

        self.client.force_login(collaborator)
        delete_url = reverse("teacher-course-class-delete", args=[course.pk, class_group.pk])
        preview = domain_payload(self.client.get(delete_url))
        operation_id = str(uuid4())
        registered = self.client.post(
            f"/api/v1/teacher/courses/{course.pk}/deletion-operations/",
            {"operation_id": operation_id, "action": "delete_class",
             "object_id": class_group.pk, "class_id": class_group.pk,
             "confirmation_token": preview["confirmation_token"]},
        )
        self.assertEqual(registered.status_code, 201)
        response = self.client.post(
            delete_url,
            {"operation_id": operation_id, "confirmation": "delete",
             "confirmation_token": preview["confirmation_token"]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ClassGroup.objects.filter(pk=class_group.pk).exists())
        self.assertFalse(ClassStudentAnalyticsPolicy.objects.filter(pk=policy_pk).exists())

    def test_rejected_task_backed_class_delete_retains_the_policy(self):
        owner = User.objects.create_user(
            username="policy-protected-delete-owner",
            role=User.Role.TEACHER,
        )
        course = Course.objects.create(
            name="策略受保护删除课程",
            code="POLPROTECT",
            term="2026秋",
            owner=owner,
        )
        class_group = ClassGroup.objects.create(
            course=course,
            owner=owner,
            name="策略受保护一班",
            academic_year="2026",
        )
        policy_pk = class_group.student_analytics_policy.pk
        bank = QuestionBank.objects.create(
            course=course,
            created_by=owner,
            name="策略受保护题库",
        )
        experiment = Experiment.objects.create(
            course=course,
            created_by=owner,
            question_bank=bank,
            name="策略受保护实验",
        )
        version = ExperimentVersion.objects.create(
            experiment=experiment,
            version_number=1,
            published_by=owner,
        )
        snapshot = ExperimentClassSnapshot.objects.create(
            version=version,
            source_class_group=class_group,
            name_snapshot=class_group.name,
            academic_year_snapshot=class_group.academic_year,
            capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
        )
        table = connection.ops.quote_name(ClassExperimentTask._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {table}
                    (class_group_id, experiment_id, release_snapshot_id,
                     class_snapshot_id, published_at, terminal_reason,
                     provenance, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    class_group.pk,
                    experiment.pk,
                    version.pk,
                    snapshot.pk.hex,
                    version.published_at,
                    "",
                    "native",
                    timezone.now(),
                ],
            )

        self.client.force_login(owner)
        response = self.client.post(
            reverse("teacher-course-class-delete", args=[course.pk, class_group.pk]),
            {"confirmation": "delete"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertTrue(ClassGroup.objects.filter(pk=class_group.pk).exists())
        self.assertTrue(ClassStudentAnalyticsPolicy.objects.filter(pk=policy_pk).exists())
