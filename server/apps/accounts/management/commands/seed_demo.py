from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, models, transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.experiments.models import (
    ClassExperimentTask,
    Experiment,
    ExperimentClassRandomizationIdentity,
    ExperimentQuestion,
    ExperimentSamplingKC,
    ExperimentVersion,
    ExperimentVersionParticipantSnapshot,
)
from apps.experiments.publication_services import (
    commit_publish_plan,
    generate_preview,
    get_or_create_publish_plan,
    save_plan_schedules,
    save_plan_selection,
)
from apps.experiments.services import assign_students
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionOption
from apps.question_bank.services.question_kc_mapping import replace_question_kc_mapping


def _delete_demo_dependency_closure(queryset):
    target_ids = list(queryset.values_list("pk", flat=True))
    if not target_ids:
        return
    scoped = queryset.model._base_manager.filter(pk__in=target_ids)
    try:
        models.QuerySet.delete(scoped)
    except ProtectedError as exc:
        if queryset.model._meta.label == "assessment.ResponseSnapshot":
            from apps.assessment.models import Attempt, SubmissionEvent

            Attempt.objects.filter(
                current_response_snapshot_id__in=target_ids
            ).update(current_response_snapshot=None)
            Attempt.objects.filter(
                final_response_snapshot_id__in=target_ids
            ).update(final_response_snapshot=None)
            SubmissionEvent.objects.filter(
                final_response_snapshot_id__in=target_ids
            ).update(final_response_snapshot=None)
            _delete_demo_dependency_closure(
                queryset.model._base_manager.filter(pk__in=target_ids)
            )
            return
        protected_by_model = {}
        for protected in exc.protected_objects:
            protected_by_model.setdefault(type(protected), []).append(protected.pk)
        for model, object_ids in protected_by_model.items():
            _delete_demo_dependency_closure(
                model._base_manager.filter(pk__in=object_ids)
            )
        _delete_demo_dependency_closure(
            queryset.model._base_manager.filter(pk__in=target_ids)
        )


class Command(BaseCommand):
    help = "创建可直接登录体验的演示数据"

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset-question-bank",
            action="store_true",
            help="显式清理并重建现有演示题库及其最小依赖闭包（仅开发/测试环境）",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        database_name = str(connection.settings_dict["NAME"])
        is_test_database = (
            database_name == ":memory:"
            or database_name.startswith("file:memorydb_")
            or database_name.startswith("test_")
        )
        if not settings.DEBUG and not is_test_database:
            raise CommandError("演示数据仅允许在开发或测试环境创建。")
        existing_course = Course.objects.filter(
            code="KT101",
            term="2026春",
        ).first()
        cleanup_required = False
        if existing_course is not None:
            existing_bank_probe = QuestionBank.objects.filter(
                course=existing_course,
                name="演示题库",
            ).first()
            cleanup_required = (
                existing_bank_probe is not None
                or Experiment.objects.filter(
                    course=existing_course,
                    name="演示交互数据采集",
                ).exists()
                or (
                    existing_bank_probe is not None
                    and Experiment.objects.filter(
                        course=existing_course,
                        question_bank=existing_bank_probe,
                    ).exists()
                )
            )
        if cleanup_required and not options["reset_question_bank"]:
            raise CommandError(
                "演示题库或实验已存在；如需重建，请显式传入 --reset-question-bank。"
            )
        admin, _ = User.objects.get_or_create(username="admin", defaults={"role": User.Role.ADMIN, "display_name": "系统管理员", "is_staff": True, "is_superuser": True})
        admin.set_password("admin123"); admin.is_active = True; admin.is_staff = True; admin.is_superuser = True; admin.save()
        teacher, _ = User.objects.get_or_create(username="teacher", defaults={"role": User.Role.TEACHER, "display_name": "演示教师"})
        teacher.set_password("teacher123"); teacher.is_active = True; teacher.save()
        course, _ = Course.objects.get_or_create(
            owner=teacher,
            code="KT101",
            term="2026春",
            defaults={"name": "知识追踪研究方法", "description": "课程中心式演示工作区"},
        )
        cls, _ = ClassGroup.objects.get_or_create(
            course=course,
            name="认知追踪实验班",
            academic_year="2026",
            defaults={"owner": teacher},
        )
        students = []
        for index in range(1, 4):
            number = f"S{index:03d}"
            student, _ = User.objects.get_or_create(username=number, defaults={"role": User.Role.STUDENT, "student_number": number, "display_name": f"学生{index}"})
            student.role = User.Role.STUDENT; student.student_number = number; student.set_password("student123"); student.is_active = True; student.uses_initial_password = False; student.initial_password_reminder_shown_at = None; student.initial_password_state_recorded_at = timezone.now(); student.must_change_password = False; student.save(); students.append(student)
        existing_bank = QuestionBank.objects.filter(
            course=course,
            name="演示题库",
        ).first()
        demo_experiment_filter = Q(name="演示交互数据采集")
        if existing_bank is not None:
            demo_experiment_filter |= Q(question_bank=existing_bank)
        demo_experiments = Experiment.objects.filter(course=course).filter(
            demo_experiment_filter
        )
        for existing_demo in list(demo_experiments):
            from apps.assessment.models import ExperimentSession

            _delete_demo_dependency_closure(
                ExperimentSession.objects.filter(version__experiment=existing_demo)
            )
            _delete_demo_dependency_closure(ExperimentVersionParticipantSnapshot.objects.filter(
                version__experiment=existing_demo
            ))
            _delete_demo_dependency_closure(
                ClassExperimentTask.objects.filter(experiment=existing_demo)
            )
            _delete_demo_dependency_closure(existing_demo.publish_plans.all())
            _delete_demo_dependency_closure(
                ExperimentClassRandomizationIdentity.objects.filter(
                    experiment=existing_demo
                )
            )
            # This command is the explicit development-only reset boundary; bypass
            # the production immutability manager only after deleting task/session
            # dependencies above.
            models.QuerySet.update(
                Experiment.objects.filter(pk=existing_demo.pk),
                frozen_release_snapshot=None,
                status=Experiment.Status.DRAFT,
            )
            _delete_demo_dependency_closure(
                ExperimentVersion.objects.filter(experiment=existing_demo)
            )
            _delete_demo_dependency_closure(
                Experiment.objects.filter(pk=existing_demo.pk)
            )
        if existing_bank is not None:
            _delete_demo_dependency_closure(
                QuestionBank.objects.filter(pk=existing_bank.pk)
            )
        # Phase 6 development reset: after the demo bank dependency closure is
        # gone, remove only now-unreferenced course dictionary nodes. Frozen
        # research snapshots store KC values in JSON and are never rewritten.
        Concept.objects.filter(
            course=course,
            question_links__isnull=True,
        ).delete()
        bank = QuestionBank.objects.create(
            course=course,
            name="演示题库",
            created_by=teacher,
            owner=teacher,
            description="用于验证知识树、抽题、单题导航与交互数据采集",
        )

        def demo_concept(sequence, name):
            code = f"KC{sequence}"
            concept, _ = Concept.objects.get_or_create(
                course=course,
                code=code,
                defaults={"name": name},
            )
            if concept.name != name or concept.parent_id is not None:
                concept.name = name
                concept.parent = None
                concept.save(update_fields=["name", "parent", "normalized_name"])
            return concept

        math_root = demo_concept(1, "数学基础")
        number_branch = demo_concept(2, "数与运算")
        measure_branch = demo_concept(3, "测量与近似")
        research_root = demo_concept(4, "认知科学")
        tracing_branch = demo_concept(5, "知识追踪")
        kc_math = demo_concept(6, "基础运算")
        kc_addition = demo_concept(7, "加法运算")
        kc_prime = demo_concept(8, "质数")
        kc_numeric = demo_concept(9, "数值近似")
        kc_tracing = demo_concept(10, "核心概念")
        math_mapping = (math_root, number_branch, kc_math, kc_addition)
        prime_mapping = (math_root, number_branch, kc_prime)
        numeric_mapping = (math_root, measure_branch, kc_numeric)
        tracing_mapping = (research_root, tracing_branch, kc_tracing)
        data = [
            (1, Question.Type.SINGLE, "2 + 2 等于多少？", {"A":"3","B":"4","C":"5","D":"6"}, "B", math_mapping, "0.100"),
            (2, Question.Type.MULTIPLE, "下列哪些数是质数？", {"A":"2","B":"4","C":"5","D":"6"}, ["A","C"], prime_mapping, "0.350"),
            (3, Question.Type.NUMERIC, "圆周率保留两位小数约为多少？", {}, "3.14", numeric_mapping, "0.250"),
            (4, Question.Type.SHORT, "英文术语 Knowledge Tracing 的中文名称是什么？", {}, "知识追踪", tracing_mapping, "0.450"),
        ]
        question_types = [
            Question.Type.SINGLE,
            Question.Type.MULTIPLE,
            Question.Type.NUMERIC,
            Question.Type.SHORT,
            Question.Type.FILL_BLANK,
            Question.Type.TRUE_FALSE,
        ]
        mappings = [math_mapping, prime_mapping, numeric_mapping, tracing_mapping]
        for seq in range(5, 94):
            qtype = question_types[(seq - 5) % len(question_types)]
            options = {"A": "正确项", "B": "干扰项", "C": "另一正确项", "D": "另一干扰项"} if qtype in {Question.Type.SINGLE, Question.Type.MULTIPLE} else {}
            answer = {
                Question.Type.SINGLE: "A",
                Question.Type.MULTIPLE: ["A", "C"],
                Question.Type.NUMERIC: str(seq),
                Question.Type.SHORT: f"答案 {seq}",
                Question.Type.FILL_BLANK: f"填空 {seq}",
                Question.Type.TRUE_FALSE: seq % 2 == 0,
            }[qtype]
            mapping = mappings[(seq - 5) % len(mappings)]
            data.append((seq, qtype, f"演示题目 {seq}（{mapping[-1].name}）", options, answer, mapping, f"{((seq % 9) + 1) / 10:.3f}"))
        for seq, qtype, text, options, answer, mapping, difficulty in data:
            q = Question.objects.create(bank=bank, sequence=seq, question_type=qtype, text=text, correct_answer=answer, teacher_difficulty=difficulty, accepted_answers=["认知追踪"] if seq==4 else [], absolute_tolerance="0.01" if qtype==Question.Type.NUMERIC else None)
            for order, (key, value) in enumerate(options.items(), start=1): QuestionOption.objects.create(question=q, key=key, text=value, display_order=order)
            root, level_two, *level_three_nodes = mapping
            replace_question_kc_mapping(
                q,
                root_kc_code=root.code,
                nodes=[
                    {"kc_code": root.code, "parent_kc_code": None, "order": 1},
                    {"kc_code": level_two.code, "parent_kc_code": root.code, "order": 1},
                    *[
                        {
                            "kc_code": level_three.code,
                            "parent_kc_code": level_two.code,
                            "order": order,
                        }
                        for order, level_three in enumerate(level_three_nodes, start=1)
                    ],
                ],
            )
        experiment = Experiment.objects.create(
            course=course,
            name="演示交互数据采集",
            created_by=teacher,
            owner=teacher,
            question_bank=bank,
            description="自由导航、草稿保存、每题提交锁定",
            sampling_mode=Experiment.SamplingMode.TOTAL_COUNT,
            sampling_total_count=len(data),
            ordering_mode=Experiment.Ordering.STRATIFIED,
            feedback_mode=Experiment.Feedback.CORRECTNESS,
            resume_mode=Experiment.ResumeMode.RESUMABLE,
        )
        created = True
        ExperimentSamplingKC.objects.bulk_create(
            [
                ExperimentSamplingKC(experiment=experiment, concept=math_root),
                ExperimentSamplingKC(experiment=experiment, concept=research_root),
            ]
        )
        course = Course.objects.select_for_update().get(pk=course.pk)
        experiment = Experiment.objects.select_for_update().get(pk=experiment.pk)
        cls = ClassGroup.objects.select_for_update().get(
            pk=cls.pk, course=course, is_active=True
        )
        for student in students:
            CourseEnrollment.objects.update_or_create(
                course=course,
                student=student,
                defaults={"class_group": cls, "is_active": True},
            )
        if created:
            for idx, q in enumerate(bank.questions.all(), start=1): ExperimentQuestion.objects.create(experiment=experiment, question=q, display_order=idx)
            assign_students(experiment, students)
        elif experiment.status == Experiment.Status.DRAFT and not (
            experiment.participant_class_links.exists()
            or experiment.participant_override_links.exists()
        ):
            assign_students(experiment, students)
        if experiment.status == Experiment.Status.DRAFT and not experiment.versions.exists():
            plan = get_or_create_publish_plan(experiment, teacher)
            plan = save_plan_selection(
                plan,
                teacher,
                expected_revision=plan.plan_revision,
                class_ids=[cls.pk],
                exclusions_by_class={},
            )
            plan = save_plan_schedules(
                plan,
                teacher,
                expected_revision=plan.plan_revision,
                default_opens_at=None,
                default_closes_at=None,
                overrides={},
            )
            generate_preview(
                plan,
                teacher,
                expected_revision=plan.plan_revision,
            )
            plan.refresh_from_db()
            commit_publish_plan(
                plan,
                teacher,
                expected_revision=plan.plan_revision,
            )
        self.stdout.write(self.style.SUCCESS("演示数据已就绪：admin/admin123，teacher/teacher123，S001-S003/student123"))
