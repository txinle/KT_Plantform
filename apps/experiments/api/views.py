import csv

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.course_access import accessible_courses, lock_writable_course
from apps.accounts.models import CourseEnrollment, User
from apps.api.exceptions import ApiConflict
from apps.api.pagination import FixedPageNumberPagination
from apps.api.permissions import RolePermission
from apps.assessment.deadline_services import DeadlineDecisionService
from apps.assessment.feedback_services import FeedbackReleaseError, release_task_feedback
from apps.assessment.manual_grading_services import task_grading_summary
from apps.assessment.models import ExperimentSession
from apps.audit.services import log_action
from apps.question_bank.models import Concept, Question, QuestionBank

from ..collection_status import (
    COLLECTION_STATUS_CHOICES,
    collection_class_choice_projection,
    collection_class_tasks,
    collection_participant_projection,
    collection_participant_queryset,
)
from ..models import (
    ClassExperimentTask,
    Experiment,
    PublishOperationJob,
    PublishPlan,
    PublishPlanExclusion,
    PublishPreviewParticipant,
)
from ..publication_jobs import (
    enqueue_publish_operation,
    publish_operation_projection,
)
from ..publication_services import (
    PublicationConflict,
    append_schedule_revision,
    cancel_task,
    class_task_business_status,
    database_now,
    effective_plan_schedule,
    get_or_create_publish_plan,
    manual_close_task,
    publish_plan_setup_projection,
    save_plan_schedules,
    save_plan_selection,
)
from ..services import validate_for_publish
from ..sampling import SamplingService, SamplingValidationError
from .serializers import (
    CollectionParticipantCollectionSerializer,
    CollectionParticipantQuerySerializer,
    ClassTaskCollectionSerializer,
    ClassTaskProgressSerializer,
    ClassTaskSerializer,
    ExpectedRevisionSerializer,
    PublishOperationJobSerializer,
    PublishPlanReviewSerializer,
    PublishPlanSerializer,
    PublishPlanSetupInputSerializer,
    PublishParticipantCollectionSerializer,
    PublishParticipantDetailSerializer,
    PublishPreviewSummarySerializer,
    ScheduleRevisionInputSerializer,
    TaskReasonSerializer,
    TeacherExperimentCollectionSerializer,
    TeacherExperimentReadinessSerializer,
    TeacherExperimentSerializer,
    TeacherSamplingInventoryRequestSerializer,
    TeacherSamplingInventoryResponseSerializer,
)


class CollectionParticipantPagination(FixedPageNumberPagination):
    page_size = 25


def _raise_api_domain_error(exc):
    if isinstance(exc, PublicationConflict):
        raise ApiConflict("; ".join(exc.messages), code=exc.code) from exc
    if isinstance(exc, SamplingValidationError):
        raise ValidationError({"participants": exc.issues}) from exc
    raise ValidationError({"non_field_errors": exc.messages}) from exc


def _plan_projection(plan):
    plan = (
        PublishPlan.objects.select_related("experiment")
        .prefetch_related("class_plans__class_group", "class_plans__exclusions")
        .get(pk=plan.pk)
    )
    setup = publish_plan_setup_projection(plan)
    class_rows = []
    for plan_class in plan.class_plans.all():
        excluded_count = plan_class.exclusions.count()
        enrolled_count = CourseEnrollment.objects.filter(
            course=plan.experiment.course,
            class_group=plan_class.class_group,
            is_active=True,
            student__is_active=True,
            student__role=User.Role.STUDENT,
        ).count()
        opens_at, closes_at = effective_plan_schedule(plan_class)
        class_rows.append(
            {
                "class_id": plan_class.class_group_id,
                "class_name": plan_class.class_group.name,
                "academic_year": plan_class.class_group.academic_year,
                "excluded_count": excluded_count,
                "participant_count": max(0, enrolled_count - excluded_count),
                "schedule_mode": (
                    "default"
                    if plan_class.use_batch_default
                    else "custom"
                ),
                "opens_at": opens_at,
                "closes_at": closes_at,
            }
        )
    return {
        "id": plan.pk,
        "experiment_id": plan.experiment_id,
        "status": plan.status,
        "current_step": plan.current_step,
        **setup,
        "default_opens_at": plan.default_opens_at,
        "default_closes_at": plan.default_closes_at,
        "classes": class_rows,
    }


def _preview_projection(preview_run):
    class_count = preview_run.class_results.aggregate(count=Count("pk"))["count"] or 0
    valid_count = preview_run.class_results.filter(quota_valid=True).count()
    participant_totals = PublishPreviewParticipant.objects.filter(
        class_result__preview_run=preview_run
    ).aggregate(participant_count=Count("pk"), item_count=Sum("item_count"))
    return {
        "id": preview_run.pk,
        "plan_revision": preview_run.plan_revision,
        "class_count": class_count,
        "participant_count": participant_totals["participant_count"] or 0,
        "item_count": participant_totals["item_count"] or 0,
        "randomization_validation": f"{valid_count}/{class_count}",
        "created_at": preview_run.created_at,
    }


def _participant_projection(participant, *, include_items=False):
    class_group = participant.class_result.publish_plan_class.class_group
    preview_items = list(participant.items.all())
    history_roles = [item.sampling_source.get("history_role") for item in preview_items]
    result = {
        "id": participant.pk,
        "student_number": participant.student.student_number,
        "display_name": participant.student.shown_name,
        "class_id": class_group.pk,
        "class_name": str(class_group),
        "item_count": participant.item_count,
        "is_valid": participant.is_valid,
        "excluded": False,
        "review_item_count": history_roles.count("review"),
        "new_item_count": history_roles.count("new"),
        "cross_experiment_item_policy": (
            preview_items[0].sampling_source.get("cross_experiment_item_policy")
            if preview_items
            else None
        ),
    }
    if include_items:
        items = []
        for item in preview_items:
            frozen = (
                item.release_mapping.question_snapshot
                if item.release_mapping_id is not None
                else None
            )
            items.append(
                {
                    "assigned_sequence_index": item.assigned_sequence_index,
                    "question_id": item.source_question_id,
                    "question_code": frozen.code if frozen else item.source_question.code,
                    "question_type": (
                        frozen.question_type
                        if frozen
                        else item.source_question.question_type
                    ),
                    "history_role": item.sampling_source.get("history_role"),
                }
            )
        result["items"] = items
    return result


def _draft_participant_projection(enrollment, *, include_items=False):
    result = {
        "id": enrollment.student_id,
        "student_number": enrollment.student.student_number,
        "display_name": enrollment.student.shown_name,
        "class_id": enrollment.class_group_id,
        "class_name": str(enrollment.class_group),
        "item_count": 0,
        "is_valid": True,
        "excluded": enrollment.excluded,
        "review_item_count": 0,
        "new_item_count": 0,
        "cross_experiment_item_policy": None,
    }
    if include_items:
        result["items"] = []
    return result


def _task_available_actions(task, *, now=None):
    grading = task_grading_summary(task)
    grading_actions = (
        ["grade_short_answers"]
        if grading["enabled"] and grading["short_answer_questions"] > 0
        else []
    )
    if task.cancelled_at or task.manual_closed_at or task.invalidated_at:
        return grading_actions
    now = now or database_now()
    schedule = task.schedule_revisions.order_by("-revision_no").first()
    actions = list(grading_actions)
    if (
        schedule is not None
        and schedule.opens_at is not None
        and now < schedule.opens_at
        and not task.sessions.exists()
    ):
        actions.extend(("revise_schedule", "cancel"))
    actions.append("manual_close")
    configuration = task.release_snapshot.configuration
    if (
        configuration.get("feedback_mode") in {
            Experiment.Feedback.CORRECTNESS,
            Experiment.Feedback.FULL,
        }
        and configuration.get("feedback_release_policy")
        == Experiment.FeedbackReleasePolicy.MANUAL_RELEASE
        and task.feedback_released_at is None
    ):
        actions.append("release_feedback")
    return actions


def _task_projection(task):
    task = (
        ClassExperimentTask.objects.select_related(
            "experiment", "class_group", "release_snapshot"
        )
        .prefetch_related("schedule_revisions")
        .get(pk=task.pk)
    )
    schedule = task.schedule_revisions.order_by("-revision_no").first()
    now = database_now()
    grading = task_grading_summary(task)
    return {
        "id": task.pk,
        "experiment_id": task.experiment_id,
        "experiment_name": task.experiment.name,
        "class_id": task.class_group_id,
        "class_name": str(task.class_group),
        "business_status": DeadlineDecisionService.get_task_state(task, at=now),
        "published_at": task.class_published_at or task.published_at,
        "opens_at": schedule.opens_at if schedule else None,
        "closes_at": schedule.closes_at if schedule else None,
        "schedule_revision": schedule.revision_no if schedule else 1,
        "participant_count": task.participant_snapshots.count(),
        "grading": grading,
        "available_actions": _task_available_actions(task, now=now),
    }


class TeacherExperimentApiMixin:
    permission_classes = (RolePermission,)
    allowed_roles = (User.Role.TEACHER,)

    def get_course(self):
        return get_object_or_404(
            accessible_courses(self.request.user),
            pk=self.kwargs["course_id"],
        )

    def get_experiment(self, course, *, lock=False):
        queryset = course.experiments.select_related(
            "question_bank", "frozen_release_snapshot"
        ).prefetch_related("question_quotas")
        if lock:
            queryset = queryset.select_for_update(of=("self",))
        return get_object_or_404(queryset, pk=self.kwargs["experiment_id"])


class TeacherSamplingInventoryView(TeacherExperimentApiMixin, APIView):
    @extend_schema(
        operation_id="teacher_experiment_sampling_inventory",
        request=TeacherSamplingInventoryRequestSerializer,
        responses=TeacherSamplingInventoryResponseSerializer,
    )
    def post(self, request, course_id):
        course = self.get_course()
        serializer = TeacherSamplingInventoryRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        bank = get_object_or_404(
            course.question_banks.exclude(visibility=QuestionBank.Visibility.ARCHIVED),
            pk=data["question_bank_id"],
        )
        concepts = list(
            Concept.objects.filter(
                course=course,
                status=Concept.Status.ACTIVE,
            )
        )
        if not set(data["kc_codes"]).issubset({concept.code for concept in concepts}):
            raise ValidationError(
                {"kc_codes": "包含不存在、已归档或不属于当前课程的知识点。"}
            )
        bank_candidates = SamplingService.total_count_candidates(
            question_bank_id=bank.pk,
            concept_ids=[],
            match_scope=Experiment.MatchScope.ANY_LEVEL,
        )
        selected_ids = [concept.pk for concept in concepts if concept.code in data["kc_codes"]]
        candidates = bank_candidates if not selected_ids else [
            question
            for question in bank_candidates
            if any(
                SamplingService._question_matches(question, concept_id, data["match_scope"])
                for concept_id in selected_ids
            )
        ]
        counts = {
            question_type: sum(
                question.question_type == question_type for question in candidates
            )
            for question_type in Question.Type.values
        }
        return Response(
            {
                "candidate_count": len(candidates),
                "kc_counts": [
                    {
                        "kc_code": concept.code,
                        "primary_available": sum(
                            SamplingService._question_matches(question, concept.pk, Experiment.MatchScope.PRIMARY)
                            for question in bank_candidates
                        ),
                        "any_level_available": sum(
                            SamplingService._question_matches(question, concept.pk, Experiment.MatchScope.ANY_LEVEL)
                            for question in bank_candidates
                        ),
                    }
                    for concept in concepts
                ],
                "question_types": [
                    {
                        "question_type": question_type,
                        "label": label,
                        "available": counts[question_type],
                    }
                    for question_type, label in Question.Type.choices
                ],
            }
        )


class TeacherExperimentCollectionView(TeacherExperimentApiMixin, APIView):
    @extend_schema(
        operation_id="teacher_experiment_list",
        responses=TeacherExperimentCollectionSerializer,
    )
    def get(self, request, course_id):
        course = self.get_course()
        queryset = course.experiments.select_related(
            "question_bank", "frozen_release_snapshot"
        ).prefetch_related("question_quotas")
        paginator = FixedPageNumberPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = TeacherExperimentSerializer(
            page,
            many=True,
            context={"request": request, "course": course},
        )
        response = paginator.get_paginated_response(serializer.data)
        response.data["capabilities"] = {"can_manage": course.can_manage(request.user) and course.status == course.Status.ACTIVE}
        response.data["summary"] = {
            "question_banks": list(
                course.question_banks.exclude(visibility="archived")
                .order_by("name", "pk")
                .values("id", "name")
            ),
            "classes": list(
                course.classes.filter(is_active=True)
                .annotate(
                    student_count=Count(
                        "enrollments",
                        filter=Q(
                            enrollments__is_active=True,
                            enrollments__student__is_active=True,
                            enrollments__student__role=User.Role.STUDENT,
                        ),
                    )
                )
                .order_by("academic_year", "name", "pk")
                .values("id", "name", "academic_year", "student_count")
            ),
        }
        return response

    @extend_schema(
        operation_id="teacher_experiment_create",
        request=TeacherExperimentSerializer,
        responses={201: TeacherExperimentSerializer},
    )
    def post(self, request, course_id):
        course = self.get_course()
        with transaction.atomic():
            course = lock_writable_course(course)
            serializer = TeacherExperimentSerializer(
                data=request.data,
                context={"request": request, "course": course},
            )
            serializer.is_valid(raise_exception=True)
            experiment = serializer.save(
                course=course,
                owner=request.user,
                created_by=request.user,
            )
            log_action(
                request,
                "experiment.create",
                experiment,
                {"course_id": course.pk, "surface": "api"},
            )
        return Response(
            TeacherExperimentSerializer(
                experiment,
                context={"request": request, "course": course},
            ).data,
            status=201,
        )


class TeacherExperimentDetailView(TeacherExperimentApiMixin, APIView):
    @extend_schema(
        operation_id="teacher_experiment_retrieve",
        responses=TeacherExperimentSerializer,
    )
    def get(self, request, course_id, experiment_id):
        course = self.get_course()
        experiment = self.get_experiment(course)
        return Response(
            TeacherExperimentSerializer(
                experiment,
                context={"request": request, "course": course},
            ).data
        )

    @extend_schema(
        operation_id="teacher_experiment_update",
        request=TeacherExperimentSerializer,
        responses=TeacherExperimentSerializer,
    )
    def patch(self, request, course_id, experiment_id):
        course = self.get_course()
        with transaction.atomic():
            course = lock_writable_course(course)
            experiment = self.get_experiment(course, lock=True)
            if experiment.status != Experiment.Status.DRAFT:
                raise ApiConflict(
                    "实验定义已冻结，不能再修改。",
                    code="EXPERIMENT_FROZEN",
                )
            serializer = TeacherExperimentSerializer(
                experiment,
                data=request.data,
                partial=True,
                context={"request": request, "course": course},
            )
            serializer.is_valid(raise_exception=True)
            experiment = serializer.save()
            log_action(
                request,
                "experiment.update",
                experiment,
                {"course_id": course.pk, "surface": "api"},
            )
        return Response(
            TeacherExperimentSerializer(
                experiment,
                context={"request": request, "course": course},
            ).data
        )


class TeacherExperimentReadinessView(TeacherExperimentApiMixin, APIView):
    @extend_schema(
        operation_id="teacher_experiment_readiness",
        responses=TeacherExperimentReadinessSerializer,
    )
    def get(self, request, course_id, experiment_id):
        course = self.get_course()
        experiment = self.get_experiment(course)
        candidate_count = Question.objects.filter(
            bank=experiment.question_bank,
            status=Question.Status.ACTIVE,
        ).count()
        participant_count = experiment.student_links.count()
        issues = []
        blocking_issues = []
        inventory_buckets = []
        requested_count = experiment.sampling_total_count
        with transaction.atomic():
            try:
                validate_for_publish(
                    experiment,
                    participant_count=1,
                )
            except DjangoValidationError as exc:
                issues.extend(exc.messages)
                blocking_issues.extend(
                    {
                        "code": "EXPERIMENT_DEFINITION_INVALID",
                        "message": message,
                        "class_id": None,
                        "kc_id": None,
                        "kc_code": None,
                        "field": None,
                    }
                    for message in exc.messages
                )
            if experiment.sampling_mode:
                try:
                    service = SamplingService(experiment)
                    resolution = service.resolve(validate_inventory=False)
                    candidate_count = len(resolution.candidates)
                    rules = list(
                        experiment.sampling_kcs.select_related("concept").order_by("pk")
                    )
                    if experiment.sampling_mode == Experiment.SamplingMode.KC_QUOTA:
                        requested_count = sum(rule.quota or 0 for rule in rules)
                    inventory_buckets = [
                        {
                            "kc_code": rule.concept.code,
                            "kc_name": rule.concept.name,
                            "match_scope": (
                                experiment.sampling_match_scope
                                if experiment.sampling_mode
                                == Experiment.SamplingMode.TOTAL_COUNT
                                else rule.match_scope
                            ),
                            "requested": rule.quota,
                            "available": (
                                len(resolution.buckets.get(rule.concept_id, []))
                                if experiment.sampling_mode
                                == Experiment.SamplingMode.KC_QUOTA
                                else len(
                                    [
                                        question
                                        for question in resolution.candidates
                                        if service._question_matches(
                                            question,
                                            rule.concept_id,
                                            experiment.sampling_match_scope,
                                        )
                                    ]
                                )
                            ),
                        }
                        for rule in rules
                    ]
                    service.resolve(
                        validate_inventory=(
                            experiment.cross_experiment_item_policy
                            != Experiment.CrossExperimentItemPolicy.MIXED_REVIEW
                        )
                    )
                except SamplingValidationError as exc:
                    issues.extend(issue["message"] for issue in exc.issues)
                    blocking_issues.extend(exc.issues)
        blocking_issues.sort(
            key=lambda issue: issue["code"] == "EXPERIMENT_DEFINITION_INVALID"
        )
        return Response(
            {
                "ready": not issues,
                "issues": issues,
                "blocking_issues": blocking_issues,
                "candidate_count": candidate_count,
                "requested_count": requested_count,
                "inventory_buckets": inventory_buckets,
                "participant_count": participant_count,
            }
        )


class TeacherPublishPlanStartView(TeacherExperimentApiMixin, APIView):
    @extend_schema(
        operation_id="teacher_publish_plan_start",
        request=None,
        responses={200: PublishPlanSerializer, 201: PublishPlanSerializer},
    )
    def post(self, request, course_id, experiment_id):
        course = self.get_course()
        experiment = self.get_experiment(course)
        active_exists = experiment.publish_plans.filter(
            status__in=(PublishPlan.Status.DRAFT, PublishPlan.Status.PREVIEWED)
        ).exists()
        try:
            plan = get_or_create_publish_plan(experiment, request.user)
        except (PublicationConflict, DjangoValidationError) as exc:
            _raise_api_domain_error(exc)
        log_action(
            request,
            "experiment.publish_plan.start",
            experiment,
            {"course_id": course.pk, "plan_id": plan.pk, "surface": "api"},
        )
        return Response(_plan_projection(plan), status=200 if active_exists else 201)


class TeacherPublishPlanMixin:
    permission_classes = (RolePermission,)
    allowed_roles = (User.Role.TEACHER,)

    def get_plan(self):
        plan = get_object_or_404(
            PublishPlan.objects.select_related("experiment__course"),
            pk=self.kwargs["plan_id"],
        )
        if not plan.experiment.course.can_manage(self.request.user):
            raise Http404
        return plan


class TeacherPublishPlanDetailView(TeacherPublishPlanMixin, APIView):
    @extend_schema(
        operation_id="teacher_publish_plan_retrieve",
        responses=PublishPlanSerializer,
    )
    def get(self, request, plan_id):
        return Response(_plan_projection(self.get_plan()))


class TeacherPublishPlanSetupView(TeacherPublishPlanMixin, APIView):
    @extend_schema(
        operation_id="teacher_publish_plan_setup",
        request=PublishPlanSetupInputSerializer,
        responses=PublishPlanSerializer,
    )
    def put(self, request, plan_id):
        plan = self.get_plan()
        serializer = PublishPlanSetupInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        classes = data["classes"]
        try:
            plan = save_plan_selection(
                plan,
                request.user,
                expected_revision=data["expected_revision"],
                class_ids=[item["class_id"] for item in classes],
                exclusions_by_class={
                    item["class_id"]: item["excluded_student_ids"]
                    for item in classes
                },
            )
            plan.refresh_from_db()
            schedule_rows = data["schedules"]
            default_schedule = data["default_schedule"]
            plan = save_plan_schedules(
                plan,
                request.user,
                expected_revision=plan.plan_revision,
                default_opens_at=default_schedule["opens_at"],
                default_closes_at=default_schedule["closes_at"],
                overrides={
                    item["class_id"]: (item["opens_at"], item["closes_at"])
                    for item in schedule_rows
                    if item["mode"] == "custom"
                },
                configured_class_ids=[item["class_id"] for item in schedule_rows],
            )
        except (PublicationConflict, DjangoValidationError) as exc:
            _raise_api_domain_error(exc)
        log_action(
            request,
            "experiment.publish_plan.setup",
            plan,
            {"plan_revision": plan.plan_revision, "surface": "api"},
        )
        return Response(_plan_projection(plan))


class TeacherPublishPlanPreviewView(TeacherPublishPlanMixin, APIView):
    @extend_schema(
        operation_id="teacher_publish_plan_preview",
        request=ExpectedRevisionSerializer,
        responses={202: PublishOperationJobSerializer},
    )
    def post(self, request, plan_id):
        plan = self.get_plan()
        serializer = ExpectedRevisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            job = enqueue_publish_operation(
                plan=plan,
                actor=request.user,
                operation=PublishOperationJob.Operation.PREVIEW,
                expected_revision=serializer.validated_data["expected_revision"],
                idempotency_key=request.headers.get("Idempotency-Key", ""),
            )
        except (PublicationConflict, DjangoValidationError) as exc:
            _raise_api_domain_error(exc)
        log_action(
            request,
            "experiment.publish_plan.preview.queued",
            plan,
            {"job_id": str(job.pk), "surface": "api"},
        )
        return Response(publish_operation_projection(job), status=202)


class TeacherPublishPlanReviewView(TeacherPublishPlanMixin, APIView):
    @extend_schema(
        operation_id="teacher_publish_plan_review",
        responses=PublishPlanReviewSerializer,
    )
    def get(self, request, plan_id):
        plan = self.get_plan()
        if plan.preview_run_id is None:
            raise ApiConflict("请先完成发布检查。", code="PREVIEW_REQUIRED")
        return Response(
            {
                "plan": _plan_projection(plan),
                "preview": _preview_projection(plan.preview_run),
                "effects": [
                    "发布后实验定义不可修改",
                    "将按班级创建正式任务与参与者快照",
                    "未开始任务仍可按规则修正排期或取消",
                ],
            }
        )


class TeacherPublishPlanCommitView(TeacherPublishPlanMixin, APIView):
    @extend_schema(
        operation_id="teacher_publish_plan_commit",
        request=ExpectedRevisionSerializer,
        responses={202: PublishOperationJobSerializer},
    )
    def post(self, request, plan_id):
        plan = self.get_plan()
        serializer = ExpectedRevisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            job = enqueue_publish_operation(
                plan=plan,
                actor=request.user,
                operation=PublishOperationJob.Operation.COMMIT,
                expected_revision=serializer.validated_data["expected_revision"],
                idempotency_key=request.headers.get("Idempotency-Key", ""),
            )
        except (PublicationConflict, DjangoValidationError) as exc:
            _raise_api_domain_error(exc)
        log_action(
            request,
            "experiment.publish_plan.commit.queued",
            plan,
            {"job_id": str(job.pk), "surface": "api"},
        )
        return Response(publish_operation_projection(job), status=202)


class TeacherPublishOperationJobView(APIView):
    permission_classes = (RolePermission,)
    allowed_roles = (User.Role.TEACHER,)

    @extend_schema(
        operation_id="teacher_publish_operation_job_retrieve",
        responses=PublishOperationJobSerializer,
    )
    def get(self, request, job_id):
        job = get_object_or_404(
            PublishOperationJob.objects.select_related("publish_plan__experiment__course"),
            pk=job_id,
        )
        if not job.publish_plan.experiment.course.can_manage(request.user):
            raise Http404
        return Response(publish_operation_projection(job))


class TeacherPublishParticipantMixin(TeacherPublishPlanMixin):
    def preview_participant_queryset(self, plan):
        return (
            PublishPreviewParticipant.objects.filter(
                class_result__preview_run_id=plan.preview_run_id
            )
            .select_related(
                "student",
                "class_result__publish_plan_class__class_group",
            )
            .prefetch_related(
                "items__source_question",
                "items__release_mapping__question_snapshot",
            )
            .order_by(
                "class_result__publish_plan_class__class_group__academic_year",
                "class_result__publish_plan_class__class_group__name",
                "student__student_number",
                "pk",
            )
        )

    def draft_participant_queryset(self, plan):
        selected_class_ids = plan.class_plans.values("class_group_id")
        exclusions = PublishPlanExclusion.objects.filter(
            publish_plan_class__publish_plan=plan,
            publish_plan_class__class_group_id=OuterRef("class_group_id"),
            student_id=OuterRef("student_id"),
        )
        return (
            CourseEnrollment.objects.filter(
                course=plan.experiment.course,
                class_group_id__in=selected_class_ids,
                is_active=True,
                student__is_active=True,
                student__role=User.Role.STUDENT,
            )
            .select_related("student", "class_group")
            .annotate(excluded=Exists(exclusions))
            .order_by(
                "class_group__academic_year",
                "class_group__name",
                "student__student_number",
                "pk",
            )
        )


class TeacherPublishParticipantListView(TeacherPublishParticipantMixin, APIView):
    @extend_schema(
        operation_id="teacher_publish_participant_list",
        responses=PublishParticipantCollectionSerializer,
    )
    def get(self, request, plan_id):
        plan = self.get_plan()
        paginator = FixedPageNumberPagination()
        if plan.preview_run_id is None:
            queryset = self.draft_participant_queryset(plan)
            projection = _draft_participant_projection
        else:
            queryset = self.preview_participant_queryset(plan)
            projection = _participant_projection
        page = paginator.paginate_queryset(
            queryset, request, view=self
        )
        return paginator.get_paginated_response(
            [projection(participant) for participant in page]
        )


class TeacherPublishParticipantDetailView(TeacherPublishParticipantMixin, APIView):
    @extend_schema(
        operation_id="teacher_publish_participant_retrieve",
        responses=PublishParticipantDetailSerializer,
    )
    def get(self, request, plan_id, participant_id):
        plan = self.get_plan()
        if plan.preview_run_id is None:
            participant = get_object_or_404(
                self.draft_participant_queryset(plan), student_id=participant_id
            )
            projection = _draft_participant_projection
        else:
            participant = get_object_or_404(
                self.preview_participant_queryset(plan), pk=participant_id
            )
            projection = _participant_projection
        return Response(projection(participant, include_items=True))


class TeacherPublishParticipantExportView(TeacherPublishParticipantMixin, APIView):
    @extend_schema(
        operation_id="teacher_publish_participant_export",
        responses={(200, "text/csv"): bytes},
    )
    def get(self, request, plan_id):
        plan = self.get_plan()
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response.write("\ufeff")
        response["Content-Disposition"] = (
            f'attachment; filename="publish-plan-{plan.pk}-participants.csv"'
        )
        writer = csv.writer(response, lineterminator="\n")
        writer.writerow(("student_number", "class_name", "item_count"))
        if plan.preview_run_id is None:
            queryset = self.draft_participant_queryset(plan)
            projection = _draft_participant_projection
        else:
            queryset = self.preview_participant_queryset(plan)
            projection = _participant_projection
        for participant in queryset:
            row = projection(participant)
            writer.writerow(
                (row["student_number"], row["class_name"], row["item_count"])
            )
        return response


class TeacherClassTaskMixin:
    permission_classes = (RolePermission,)
    allowed_roles = (User.Role.TEACHER,)

    def get_task(self):
        task = get_object_or_404(
            ClassExperimentTask.objects.select_related(
                "experiment__course", "class_group"
            ),
            pk=self.kwargs["task_id"],
        )
        if not task.experiment.course.can_manage(self.request.user):
            raise Http404
        return task


class TeacherExperimentClassTaskListView(TeacherClassTaskMixin, APIView):
    @extend_schema(
        operation_id="teacher_experiment_class_task_list",
        responses=ClassTaskCollectionSerializer,
    )
    def get(self, request, experiment_id):
        experiment = get_object_or_404(
            Experiment.objects.select_related("course"), pk=experiment_id
        )
        if not experiment.course.can_manage(request.user):
            raise Http404
        tasks = experiment.class_tasks.select_related("class_group").order_by(
            "class_group__academic_year", "class_group__name", "pk"
        )
        return Response({"items": [_task_projection(task) for task in tasks]})


class TeacherExperimentCollectionParticipantView(APIView):
    permission_classes = (RolePermission,)
    allowed_roles = (User.Role.TEACHER,)

    @extend_schema(
        operation_id="teacher_experiment_collection_participant_list",
        parameters=[CollectionParticipantQuerySerializer],
        responses=CollectionParticipantCollectionSerializer,
    )
    def get(self, request, experiment_id):
        experiment = get_object_or_404(
            Experiment.objects.select_related("course"), pk=experiment_id
        )
        if experiment.course_id is None or not experiment.course.can_manage(
            request.user
        ):
            raise Http404

        query_serializer = CollectionParticipantQuerySerializer(
            data=request.query_params
        )
        query_serializer.is_valid(raise_exception=True)
        filters = query_serializer.validated_data

        class_tasks = collection_class_tasks(experiment)
        class_ids = {task.class_group_id for task in class_tasks}
        if filters["class_id"] is not None and filters["class_id"] not in class_ids:
            raise ValidationError(
                {"class_id": "班级不属于当前实验的已发布任务。"}
            )

        queryset = collection_participant_queryset(
            experiment,
            class_id=filters["class_id"],
            status=filters["status"],
            query=filters["q"],
        )
        paginator = CollectionParticipantPagination()
        try:
            page = paginator.paginate_queryset(queryset, request, view=self)
        except NotFound as exc:
            raise ValidationError({"page": "页码超出范围。"}) from exc
        payload = paginator.get_paginated_response(
            [collection_participant_projection(snapshot) for snapshot in page]
        ).data
        payload.update(
            {
                "filters": {
                    "class_id": filters["class_id"],
                    "status": filters["status"],
                    "q": filters["q"],
                },
                "class_choices": [
                    collection_class_choice_projection(task) for task in class_tasks
                ],
                "status_choices": [
                    {"value": value, "label": label}
                    for value, label in COLLECTION_STATUS_CHOICES
                ],
            }
        )
        return Response(payload)


class TeacherClassTaskDetailView(TeacherClassTaskMixin, APIView):
    @extend_schema(
        operation_id="teacher_class_task_retrieve",
        responses=ClassTaskSerializer,
    )
    def get(self, request, task_id):
        return Response(_task_projection(self.get_task()))


class TeacherClassTaskProgressView(TeacherClassTaskMixin, APIView):
    @extend_schema(
        operation_id="teacher_class_task_progress",
        responses=ClassTaskProgressSerializer,
    )
    def get(self, request, task_id):
        task = self.get_task()
        counts = {
            status: count
            for status, count in task.sessions.values_list("status").annotate(
                count=Count("pk")
            )
        }
        total = task.participant_snapshots.count()
        started = sum(counts.values())
        return Response(
            {
                "task_id": task.pk,
                "total_participants": total,
                "not_started": max(0, total - started),
                "in_progress": counts.get(ExperimentSession.Status.IN_PROGRESS, 0),
                "completed": counts.get(ExperimentSession.Status.COMPLETED, 0),
                "expired": counts.get(ExperimentSession.Status.EXPIRED, 0),
                "invalidated": counts.get(ExperimentSession.Status.INVALIDATED, 0),
            }
        )


class TeacherClassTaskScheduleRevisionView(TeacherClassTaskMixin, APIView):
    @extend_schema(
        operation_id="teacher_class_task_schedule_revision_create",
        request=ScheduleRevisionInputSerializer,
        responses={201: ClassTaskSerializer},
    )
    def post(self, request, task_id):
        task = self.get_task()
        serializer = ScheduleRevisionInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            revision = append_schedule_revision(
                task,
                request.user,
                opens_at=serializer.validated_data["opens_at"],
                closes_at=serializer.validated_data["closes_at"],
                reason=serializer.validated_data.get("reason", ""),
            )
        except (PublicationConflict, DjangoValidationError) as exc:
            _raise_api_domain_error(exc)
        result = _task_projection(task)
        result["schedule_revision"] = revision.revision_no
        log_action(
            request,
            "class_task.schedule_revision",
            task,
            {"revision_no": revision.revision_no, "surface": "api"},
        )
        return Response(result, status=201)


class TeacherClassTaskCancelView(TeacherClassTaskMixin, APIView):
    @extend_schema(
        operation_id="teacher_class_task_cancel",
        request=TaskReasonSerializer,
        responses=ClassTaskSerializer,
    )
    def post(self, request, task_id):
        task = self.get_task()
        serializer = TaskReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            task = cancel_task(
                task, request.user, reason=serializer.validated_data.get("reason", "")
            )
        except (PublicationConflict, DjangoValidationError) as exc:
            _raise_api_domain_error(exc)
        log_action(request, "class_task.cancel", task, {"surface": "api"})
        return Response(_task_projection(task))


class TeacherClassTaskManualCloseView(TeacherClassTaskMixin, APIView):
    @extend_schema(
        operation_id="teacher_class_task_manual_close",
        request=TaskReasonSerializer,
        responses=ClassTaskSerializer,
    )
    def post(self, request, task_id):
        task = self.get_task()
        serializer = TaskReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            task = manual_close_task(
                task, request.user, reason=serializer.validated_data.get("reason", "")
            )
        except (PublicationConflict, DjangoValidationError) as exc:
            _raise_api_domain_error(exc)
        log_action(request, "class_task.manual_close", task, {"surface": "api"})
        return Response(_task_projection(task))


class TeacherClassTaskFeedbackReleaseView(TeacherClassTaskMixin, APIView):
    @extend_schema(
        operation_id="teacher_class_task_feedback_release",
        request=TaskReasonSerializer,
        responses=ClassTaskSerializer,
    )
    def post(self, request, task_id):
        task = self.get_task()
        serializer = TaskReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            release_task_feedback(
                task,
                request.user,
                reason=serializer.validated_data.get("reason", ""),
            )
        except FeedbackReleaseError as exc:
            raise ValidationError({"non_field_errors": [str(exc)]}) from exc
        return Response(_task_projection(task))
