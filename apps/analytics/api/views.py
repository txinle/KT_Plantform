from math import ceil
from urllib.parse import urlencode

from django.conf import settings
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.class_management_services import (
    can_create_course_class_group,
    create_course_class_group,
)
from apps.accounts.course_views import (
    _scope_display,
    _scope_from_request,
    _teaching_scope_display,
    _teaching_scope_from_request,
)
from apps.accounts.forms import ClassGroupForm
from apps.accounts.models import ClassGroup, Course, User
from apps.api.pagination import FixedPageNumberPagination
from apps.assessment.models import ExperimentSession, SubmissionEvent
from apps.audit.services import log_action

from ..advanced_catalog import build_advanced_catalog, enabled_extension_tables
from ..data_scope import DataScope, ScopeNotFound, ScopeValidationError
from ..export_catalog import (
    COMMON_FIELD_DESCRIPTIONS,
    PACKAGE_TABLES,
    TABLE_FIELD_DESCRIPTIONS,
)
from ..models import ExportJob
from ..quality_reports import EvaluationState, build_quality_report
from ..services import (
    ExportContractError,
    create_export_job,
    create_export_snapshot,
    preview_export_table,
    run_export_job,
)
from ..teaching_scope import TeachingScope
from ..teaching.metrics import build_class_center_snapshot
from .serializers import (
    DataCenterAdvancedSerializer,
    DataCenterExportCatalogSerializer,
    DataCenterExportHistoryCollectionSerializer,
    DataCenterExportJobCreateSerializer,
    DataCenterExportJobMutationSerializer,
    DataCenterOverviewSerializer,
    DataCenterQualityCollectionSerializer,
    DataCenterScopeSerializer,
    DataCenterScopeUpdateSerializer,
    TeacherClassCenterSerializer,
    TeacherClassCreateRequestSerializer,
    TeacherClassCreateResponseSerializer,
    TeacherClassCollectionSerializer,
)


def _course_for_teacher(user, course_id):
    course = get_object_or_404(Course, pk=course_id)
    if not course.can_manage(user):
        raise NotFound()
    return course


def _course_payload(course):
    return {
        "id": course.pk,
        "name": course.name,
        "code": course.code,
        "term": course.term,
        "status": course.status,
    }


def _teaching_scope_payload(scope):
    return {
        "experiment_id": scope.experiment_id,
        "class_id": scope.class_id,
        "is_complete": scope.is_complete,
        "display": _teaching_scope_display(scope),
    }


def _research_scope_payload(scope):
    values = scope.serialize()
    return {
        "values": values,
        "query": urlencode(values),
        "display": _scope_display(scope),
        "is_single_version": scope.is_single_version,
    }


def _version_payload(version):
    return {"id": version.pk, "version_number": version.version_number}


def _experiment_payload(experiment):
    return {
        "id": experiment.pk,
        "name": experiment.name,
        "versions": [_version_payload(version) for version in experiment.versions.all()],
    }


def _recent_export_payload(job):
    return {
        "job_id": job.job_id,
        "package": job.package,
        "experiment_name": job.experiment.name if job.experiment else None,
        "status": job.status,
        "status_label": job.get_status_display(),
        "created_at": job.created_at,
    }


class TeacherDataCenterScopeView(APIView):
    @extend_schema(
        operation_id="teacher_data_center_scope_retrieve",
        responses=DataCenterScopeSerializer,
    )
    def get(self, request, course_id):
        course = _course_for_teacher(request.user, course_id)
        return Response(self._payload(request, course))

    @extend_schema(
        operation_id="teacher_data_center_scope_update",
        request=DataCenterScopeUpdateSerializer,
        responses=DataCenterScopeSerializer,
    )
    def post(self, request, course_id):
        course = _course_for_teacher(request.user, course_id)
        serializer = DataCenterScopeUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            scope = TeachingScope.from_mapping(course, serializer.validated_data)
        except ScopeNotFound as error:
            raise NotFound(str(error)) from error
        except ScopeValidationError as error:
            raise ValidationError({"scope": [str(error)]}) from error
        request.session[f"data_center_teaching_scope_v1:{course.pk}"] = scope.serialize()
        request.session.pop(f"data_center_scope_v1:{course.pk}", None)
        request.session.modified = True
        return Response(self._payload(request, course, scope=scope))

    @staticmethod
    def _payload(request, course, *, scope=None):
        scope = scope or _teaching_scope_from_request(request, course)
        experiments = list(
            course.experiments.prefetch_related("versions").order_by("name", "pk")
        )
        classes = course.classes.filter(is_active=True).order_by(
            "academic_year", "name", "pk"
        )
        return {
                "course": _course_payload(course),
                "current": _teaching_scope_payload(scope),
                "experiments": [_experiment_payload(item) for item in experiments],
                "classes": [
                    {
                        "id": item.pk,
                        "name": item.name,
                        "academic_year": item.academic_year,
                    }
                    for item in classes
                ],
                "scope_update_url": reverse(
                    "teacher-data-center-scope", args=[course.pk]
                ),
            }


def _export_job_payload(job):
    return {
        "job_id": job.job_id,
        "package": job.package,
        "status": job.status,
        "status_label": job.get_status_display(),
        "download_url": (
            reverse(
                "analytics:export_job_download",
                args=[job.course_id, job.job_id],
            )
            if job.status == ExportJob.Status.SUCCEEDED and job.artifact_path
            else None
        ),
        "history_url": reverse("courses:data_export_history", args=[job.course_id]),
    }


class TeacherDataCenterExportJobCreateView(APIView):
    @extend_schema(
        operation_id="teacher_data_center_export_job_create",
        request=DataCenterExportJobCreateSerializer,
        responses={201: DataCenterExportJobMutationSerializer},
    )
    def post(self, request, course_id):
        course = _course_for_teacher(request.user, course_id)
        serializer = DataCenterExportJobCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        if not values["confirm_identified"]:
            raise ValidationError(
                {"confirm_identified": ["请确认该导出包含实名数据后再生成。"]}
            )
        package = values["package"]
        if package not in PACKAGE_TABLES or package == "identity":
            raise ValidationError({"package": ["不支持的数据包类型。"]})
        try:
            scope = DataScope.from_mapping(course, values["scope"])
        except ScopeNotFound as error:
            raise NotFound("导出范围不属于当前课程") from error
        except ScopeValidationError as error:
            raise ValidationError({"scope": [str(error)]}) from error
        experiment = (
            get_object_or_404(course.experiments, pk=scope.experiment_id)
            if scope.experiment_id
            else None
        )
        extensions = enabled_extension_tables(experiment)
        if package == "confidence" and "confidence_assignments" not in extensions:
            raise ValidationError({"package": ["当前实验未启用 Confidence 采集。"]})
        if package == "simulation" and "simulation_events" not in extensions:
            raise ValidationError({"package": ["当前实验未启用 Simulation 采集。"]})
        try:
            snapshot = create_export_snapshot(
                course=course,
                experiment=experiment,
                created_by=request.user,
                scope=scope,
                package=package,
            )
            job = create_export_job(
                course=course,
                experiment=experiment,
                snapshot=snapshot,
                created_by=request.user,
                package=package,
                selected_tables=values["tables"],
                selected_fields=values["fields"],
            )
            run_export_job(job)
        except ExportContractError as error:
            if "job" in locals():
                job.status = ExportJob.Status.FAILED
                job.progress_stage = "contract_error"
                job.finished_at = timezone.now()
                job.save(update_fields=["status", "progress_stage", "finished_at"])
            raise ValidationError({"scope": [str(error)]}) from error
        log_action(
            request,
            "analytics.identified_export_job_create",
            course,
            {
                "job_id": str(job.job_id),
                "package": package,
                "experiment_id": experiment.pk if experiment else None,
            },
        )
        return Response(_export_job_payload(job), status=status.HTTP_201_CREATED)


class TeacherDataCenterExportJobDetailView(APIView):
    @extend_schema(
        operation_id="teacher_data_center_export_job_destroy",
        responses=DataCenterExportJobMutationSerializer,
    )
    def delete(self, request, course_id, job_id):
        course = _course_for_teacher(request.user, course_id)
        job = get_object_or_404(ExportJob, course=course, job_id=job_id)
        job.expire_artifact(reason="user_requested_removal")
        log_action(
            request,
            "analytics.export_job_delete",
            course,
            {"job_id": str(job.job_id), "package": job.package},
        )
        job.refresh_from_db()
        return Response(_export_job_payload(job))


class TeacherDataCenterOverviewView(APIView):
    @extend_schema(
        operation_id="teacher_data_center_overview_retrieve",
        parameters=[
            OpenApiParameter("experiment_id", int, required=False),
            OpenApiParameter("class_id", int, required=False),
        ],
        responses=DataCenterOverviewSerializer,
    )
    def get(self, request, course_id):
        course = _course_for_teacher(request.user, course_id)
        scope = _teaching_scope_from_request(request, course)
        sessions = scope.sessions()
        attempts = scope.attempts()
        report = build_quality_report(scope)
        warning_attempt_ids = {
            row.attempt_id
            for row in report.rows
            if row.attempt_id is not None
            and row.state in {EvaluationState.WARN, EvaluationState.FAIL}
        }
        recent_jobs = course.export_jobs.select_related("experiment").order_by(
            "-created_at", "-job_id"
        )[:5]
        return Response(
            {
                "course": _course_payload(course),
                "scope": _teaching_scope_payload(scope),
                "facts": {
                    "participants": scope.roster().count(),
                    "observed_participants": sessions.values("student_id")
                    .distinct()
                    .count(),
                    "completed_sessions": sessions.filter(
                        status=ExperimentSession.Status.COMPLETED
                    ).count(),
                    "total_sessions": sessions.count(),
                    "submitted_attempts": SubmissionEvent.objects.filter(
                        attempt__in=attempts
                    ).count(),
                    "quality_warnings": len(warning_attempt_ids),
                    "not_evaluable": report.counts.get(
                        EvaluationState.NOT_EVALUABLE, 0
                    ),
                },
                "recent_exports": [
                    _recent_export_payload(job) for job in recent_jobs
                ],
                "actions": {
                    "scope_update_url": reverse(
                        "teacher-data-center-scope", args=[course.pk]
                    ),
                    "governance_url": f"{reverse('courses:settings', args=[course.pk])}#identity-governance",
                    "teaching_url": reverse(
                        "courses:data_teaching", args=[course.pk]
                    ),
                    "exports_url": reverse(
                        "courses:data_exports", args=[course.pk]
                    ),
                    "quality_url": reverse(
                        "courses:data_quality", args=[course.pk]
                    ),
                    "history_url": reverse(
                        "courses:data_export_history", args=[course.pk]
                    ),
                },
            }
        )


class TeacherDataCenterExportCatalogView(APIView):
    @extend_schema(
        operation_id="teacher_data_center_exports_retrieve",
        responses=DataCenterExportCatalogSerializer,
    )
    def get(self, request, course_id):
        course = _course_for_teacher(request.user, course_id)
        scope = _scope_from_request(request, course)
        selected_experiment = (
            course.experiments.prefetch_related("versions")
            .filter(pk=scope.experiment_id)
            .first()
            if scope.experiment_id
            else None
        )
        packages = [
            ("teaching", "教学成绩（XLSX）", "当前有效成绩与导出血缘"),
            ("complete", "完整研究包", "全过程数据与可复现派生"),
            ("process", "作答过程", "原始事件、响应快照与稳定输入观测"),
            ("kt", "KT", "知识追踪所需数据"),
            ("cd", "CD", "认知诊断所需数据"),
        ]
        extensions = enabled_extension_tables(selected_experiment)
        if "confidence_assignments" in extensions:
            packages.append(("confidence", "Confidence", "信心采集及校准数据"))
        if "simulation_events" in extensions:
            packages.append(("simulation", "Simulation", "仿真事件和快照"))
        package_rows = [
            {
                "key": key,
                "label": label,
                "description": description,
                "blocked": key in {"teaching", "kt", "cd"}
                and not scope.is_single_version,
            }
            for key, label, description in packages
        ]
        selected_key = request.query_params.get("package", "")
        selected_package = next(
            (row for row in package_rows if row["key"] == selected_key), None
        )
        versions = list(selected_experiment.versions.all()) if selected_experiment else []
        selected_version = next(
            (version for version in versions if version.pk == scope.version_id), None
        )
        return Response(
            {
                "course": _course_payload(course),
                "scope": _research_scope_payload(scope),
                "packages": package_rows,
                "selected_package": selected_package,
                "export_versions": [_version_payload(item) for item in versions],
                "selected_export_version": (
                    _version_payload(selected_version) if selected_version else None
                ),
                "identified_data_notice": (
                    "所有数据包均含实名身份数据；对外共享前必须在平台外完成合规去标识化。"
                ),
                "actions": {
                    "create_url": reverse(
                        "teacher-data-center-export-job-create", args=[course.pk]
                    ),
                    "reset_url": reverse("courses:data_exports", args=[course.pk]),
                },
            }
        )


class TeacherDataCenterQualityView(APIView):
    @extend_schema(
        operation_id="teacher_data_center_quality_list",
        responses=DataCenterQualityCollectionSerializer,
    )
    def get(self, request, course_id):
        course = _course_for_teacher(request.user, course_id)
        scope = _scope_from_request(request, course)
        report = build_quality_report(scope)
        paginator = FixedPageNumberPagination()
        page = paginator.paginate_queryset(list(report.rows), request, view=self)
        items = [
            {
                "rule": row.rule,
                "rule_version": row.rule_version,
                "state": str(row.state),
                "entity_id": row.entity_id,
                "source_mode": row.source_mode,
                "reason_code": row.reason_code,
                "evidence_summary": row.evidence_summary,
            }
            for row in page
        ]
        response = paginator.get_paginated_response(items)
        response.data.update(
            {
                "course": _course_payload(course),
                "scope": _research_scope_payload(scope),
                "counts": {
                    str(state): report.counts.get(state, 0)
                    for state in EvaluationState
                },
            }
        )
        return response


class TeacherDataCenterExportHistoryView(APIView):
    @extend_schema(
        operation_id="teacher_data_center_export_history_list",
        responses=DataCenterExportHistoryCollectionSerializer,
    )
    def get(self, request, course_id):
        course = _course_for_teacher(request.user, course_id)
        jobs = course.export_jobs.select_related(
            "experiment", "created_by"
        ).order_by("-created_at", "-job_id")
        paginator = FixedPageNumberPagination()
        page = paginator.paginate_queryset(jobs, request, view=self)
        items = []
        for job in page:
            can_manage_artifact = job.status == ExportJob.Status.SUCCEEDED
            items.append(
                {
                    **_recent_export_payload(job),
                    "artifact_removal_reason": job.artifact_removal_reason,
                    "created_by_name": job.created_by.shown_name,
                    "download_url": reverse(
                        "analytics:export_job_download",
                        args=[course.pk, job.job_id],
                    )
                    if can_manage_artifact and job.artifact_path
                    else None,
                    "delete_url": reverse(
                        "teacher-data-center-export-job-detail",
                        args=[course.pk, job.job_id],
                    )
                    if can_manage_artifact
                    else None,
                }
            )
        response = paginator.get_paginated_response(items)
        response.data["course"] = _course_payload(course)
        return response


class TeacherDataCenterAdvancedView(APIView):
    @extend_schema(
        operation_id="teacher_data_center_advanced_retrieve",
        parameters=[
            OpenApiParameter("experiment_id", int, required=False),
            OpenApiParameter("class_id", int, required=False),
            OpenApiParameter("table", str, required=False),
            OpenApiParameter("search", str, required=False),
            OpenApiParameter("page", int, required=False),
        ],
        responses=DataCenterAdvancedSerializer,
    )
    def get(self, request, course_id):
        course = _course_for_teacher(request.user, course_id)
        scope = _scope_from_request(request, course)
        search = request.query_params.get("search", "").strip()[:100]
        selected_experiment = (
            course.experiments.prefetch_related("versions")
            .filter(pk=scope.experiment_id)
            .first()
            if scope.experiment_id
            else None
        )
        groups, selected_table, selected_info = build_advanced_catalog(
            experiment=selected_experiment,
            selected_table=request.query_params.get("table", "attempts"),
        )
        preview_payload = None
        if selected_experiment is not None:
            preview = preview_export_table(
                course=course,
                experiment=selected_experiment,
                table=selected_table,
                page=request.query_params.get("page", 1),
                page_size=20,
                scope=scope,
                student_search=search,
            )
            total_pages = max(1, ceil(preview["total"] / preview["page_size"]))
            start = (
                (preview["page"] - 1) * preview["page_size"] + 1
                if preview["total"]
                else 0
            )
            preview_payload = {
                "columns": [
                    {
                        "name": header,
                        "description": TABLE_FIELD_DESCRIPTIONS.get(
                            selected_table, {}
                        ).get(header, COMMON_FIELD_DESCRIPTIONS.get(header, "数据字段")),
                    }
                    for header in preview["headers"]
                ],
                "rows": preview["rows"],
                "page": {
                    "number": preview["page"],
                    "size": preview["page_size"],
                    "total_items": preview["total"],
                    "total_pages": total_pages,
                    "start": start,
                    "end": min(
                        preview["page"] * preview["page_size"], preview["total"]
                    ),
                },
                "data_status": preview["data_status"],
            }
        payload = {
            "course": _course_payload(course),
            "scope": _research_scope_payload(scope),
            "search": search,
            "groups": groups,
            "selected_table": selected_info,
            "selected_experiment": (
                _experiment_payload(selected_experiment)
                if selected_experiment
                else None
            ),
            "preview": preview_payload,
        }
        return Response(DataCenterAdvancedSerializer(payload).data)


def _metric_payload(metric):
    return {
        "numerator": metric.numerator,
        "denominator": metric.denominator,
        "ratio": metric.ratio,
        "state": metric.state,
        "sample_size": metric.sample_size,
        "percent_display": metric.percent_display,
    }


def _page_payload(page):
    total = page.total_count
    start = (page.page - 1) * page.page_size + 1 if total else 0
    end = min(page.page * page.page_size, total)
    return {
        "number": page.page,
        "size": page.page_size,
        "total_items": total,
        "total_pages": page.page_count,
        "start": start,
        "end": end,
    }


def _class_detail_url(course_id, class_id):
    return reverse("courses:class_detail", args=[course_id, class_id])


def _teacher_class_summary_payload(course, class_group, active_student_count):
    return {
        "id": class_group.pk,
        "name": class_group.name,
        "academic_year": class_group.academic_year,
        "is_active": class_group.is_active,
        "active_student_count": active_student_count,
        "detail_url": _class_detail_url(course.pk, class_group.pk),
    }


class TeacherClassCollectionView(APIView):
    @extend_schema(
        operation_id="teacher_course_class_list",
        parameters=[OpenApiParameter("q", str, required=False)],
        responses=TeacherClassCollectionSerializer,
    )
    def get(self, request, course_id):
        course = _course_for_teacher(request.user, course_id)
        query = request.query_params.get("q", "").strip()
        classes = course.classes.annotate(
            active_student_count=Count(
                "enrollments",
                filter=Q(
                    enrollments__is_active=True,
                    enrollments__student__is_active=True,
                    enrollments__student__role=User.Role.STUDENT,
                ),
                distinct=True,
            )
        ).order_by("academic_year", "name", "pk")
        if query:
            classes = classes.filter(
                Q(name__icontains=query) | Q(academic_year__icontains=query)
            )

        paginator = FixedPageNumberPagination()
        page = paginator.paginate_queryset(classes, request, view=self)
        items = [
            _teacher_class_summary_payload(
                course,
                class_group,
                class_group.active_student_count,
            )
            for class_group in page
        ]
        response = paginator.get_paginated_response(items)
        response.data["course"] = _course_payload(course)
        response.data["capabilities"] = {
            "can_create": can_create_course_class_group(course, request.user)
        }
        return response

    @extend_schema(
        operation_id="teacher_course_class_create",
        request=TeacherClassCreateRequestSerializer,
        responses={201: TeacherClassCreateResponseSerializer},
    )
    def post(self, request, course_id):
        course = _course_for_teacher(request.user, course_id)
        form = ClassGroupForm(request.data)
        class_group = create_course_class_group(
            request=request,
            course=course,
            form=form,
        )
        if class_group is None:
            raise ValidationError(form.errors)
        return Response(
            TeacherClassCreateResponseSerializer(
                {
                    "class_group": _teacher_class_summary_payload(
                        course,
                        class_group,
                        0,
                    )
                }
            ).data,
            status=status.HTTP_201_CREATED,
        )


class TeacherClassCenterView(APIView):
    @extend_schema(
        parameters=[
            OpenApiParameter("experiment_id", int, required=False),
            OpenApiParameter("q", str, required=False),
            OpenApiParameter("sort", str, required=False),
            OpenApiParameter("status", str, required=False),
            OpenApiParameter("page", int, required=False),
        ],
        responses=TeacherClassCenterSerializer,
    )
    def get(self, request, course_id, class_id):
        if not settings.CLASS_CENTER_ENABLED:
            raise NotFound()
        course = _course_for_teacher(request.user, course_id)
        class_group = get_object_or_404(course.classes, pk=class_id)

        experiment_id = self._positive_int_or_none(
            request.query_params.get("experiment_id")
        )
        page = self._positive_int_or_default(request.query_params.get("page"), 1)
        query = request.query_params.get("q", "").strip()
        sort = request.query_params.get("sort", "student_number")
        status = request.query_params.get("status") or None
        snapshot = build_class_center_snapshot(
            class_group,
            experiment_id=experiment_id,
            query=query,
            sort=sort,
            status=status,
            page=page,
            page_size=20,
        )
        if experiment_id is not None and snapshot.scope.task_count == 0:
            raise NotFound()

        detail_url = _class_detail_url(course.pk, class_group.pk)
        active_student_count = class_group.enrollments.filter(
            is_active=True,
            student__is_active=True,
            student__role=User.Role.STUDENT,
        ).count()
        payload = self._payload(
            course,
            class_group,
            snapshot,
            detail_url=detail_url,
            active_student_count=active_student_count,
            query=query,
            sort=sort,
            status=status,
            experiment_id=experiment_id,
        )
        return Response(TeacherClassCenterSerializer(payload).data)

    @staticmethod
    def _positive_int_or_none(raw_value):
        if raw_value in (None, ""):
            return None
        try:
            value = int(raw_value)
        except (TypeError, ValueError) as error:
            raise NotFound() from error
        if value <= 0:
            raise NotFound()
        return value

    @classmethod
    def _positive_int_or_default(cls, raw_value, default):
        if raw_value in (None, ""):
            return default
        return cls._positive_int_or_none(raw_value)

    @staticmethod
    def _payload(
        course,
        class_group,
        snapshot,
        *,
        detail_url,
        active_student_count,
        query,
        sort,
        status,
        experiment_id,
    ):
        student_url = lambda student_id: reverse(
            "courses:class_student_detail",
            args=[course.pk, class_group.pk, student_id],
        )
        return {
            "course": _course_payload(course),
            "class_group": {
                "id": class_group.pk,
                "name": class_group.name,
                "academic_year": class_group.academic_year,
                "is_active": class_group.is_active,
                "active_student_count": active_student_count,
            },
            "generated_at": snapshot.generated_at,
            "scope": {
                "course_id": snapshot.scope.course_id,
                "class_group_id": snapshot.scope.class_group_id,
                "experiment_id": snapshot.scope.experiment_id,
                "task_count": snapshot.scope.task_count,
            },
            "has_live_experiments": snapshot.has_live_experiments,
            "rank_sample_size": snapshot.rank_sample_size,
            "overview": {
                "roster_count": snapshot.overview.roster_count,
                "participated_count": snapshot.overview.participated_count,
                "correct_rate": _metric_payload(snapshot.overview.correct_rate),
                "completion_rate": _metric_payload(snapshot.overview.completion_rate),
            },
            "class_average_correct_rate": _metric_payload(
                snapshot.class_average_correct_rate
            ),
            "correct_rate_distribution": [
                {"key": row.key, "label": row.label, "count": row.count}
                for row in snapshot.correct_rate_distribution
            ],
            "participation": {
                "completed": snapshot.participation.completed,
                "in_progress": snapshot.participation.in_progress,
                "not_participated": snapshot.participation.not_participated,
            },
            "kc_rows": [
                {"name": row.name, "correct_rate": _metric_payload(row.correct_rate)}
                for row in snapshot.kc_rows
            ],
            "experiment_options": [
                {
                    "experiment_id": row.experiment_id,
                    "name": row.name,
                    "published_at": row.published_at,
                }
                for row in snapshot.experiment_options
            ],
            "experiment_rows": [
                {
                    "experiment_id": row.experiment_id,
                    "name": row.name,
                    "published_at": row.published_at,
                    "is_live": row.is_live,
                    "eligible_count": row.eligible_count,
                    "participated_count": row.participated_count,
                    "completed_count": row.completed_count,
                    "correct_rate": _metric_payload(row.correct_rate),
                    "score_rate": _metric_payload(row.score_rate),
                    "analysis_url": f"{detail_url}?{urlencode({'section': 'experiments', 'experiment_id': row.experiment_id})}",
                }
                for row in snapshot.experiment_rows
            ],
            "experiment_participants": [
                {
                    "student_id": row.student_id,
                    "student_number": row.student_number,
                    "display_name": row.display_name,
                    "is_current_member": row.is_current_member,
                    "participated": row.participated,
                    "completed": row.completed,
                    "correct_rate": _metric_payload(row.correct_rate),
                    "score_rate": _metric_payload(row.score_rate),
                    "rank": row.rank,
                    "rank_sample_size": row.rank_sample_size,
                    "student_url": student_url(row.student_id),
                }
                for row in snapshot.experiment_participants
            ],
            "students": {
                "items": [
                    {
                        "student_id": row.student_id,
                        "student_number": row.student_number,
                        "display_name": row.display_name,
                        "completed_experiments": row.completed_experiments,
                        "eligible_experiments": row.eligible_experiments,
                        "correct_rate": _metric_payload(row.correct_rate),
                        "score_rate": _metric_payload(row.score_rate),
                        "average_answer_time_ms": row.average_answer_time_ms,
                        "average_answer_time_display": row.average_answer_time_display,
                        "status": row.status,
                        "rank": row.rank,
                        "student_url": student_url(row.student_id),
                    }
                    for row in snapshot.students.items
                ],
                "page": _page_payload(snapshot.students),
            },
            "question_rows": [
                {
                    "question_snapshot_id": row.question_snapshot_id,
                    "code": row.code,
                    "text": row.text,
                    "kcs": row.kcs,
                    "answer_count": row.answer_count,
                    "correct_count": row.correct_count,
                    "incorrect_count": row.incorrect_count,
                    "correct_rate": _metric_payload(row.correct_rate),
                    "average_answer_time_ms": row.average_answer_time_ms,
                    "average_answer_time_display": row.average_answer_time_display,
                    "detail_url": reverse(
                        "courses:class_question_detail",
                        args=[
                            course.pk,
                            class_group.pk,
                            snapshot.scope.experiment_id,
                            row.question_snapshot_id,
                        ],
                    ),
                }
                for row in snapshot.question_rows
            ],
            "filters": {
                "query": query,
                "sort": sort,
                "status": status,
                "experiment_id": experiment_id,
            },
            "actions": {
                "class_list_url": reverse("courses:classes", args=[course.pk]),
                "visibility_url": reverse(
                    "courses:class_student_visibility",
                    args=[course.pk, class_group.pk],
                ),
                "manage_url": f"{detail_url}?{urlencode({'section': 'manage'})}",
                "import_students_url": reverse(
                    "courses:student_import",
                    args=[course.pk, class_group.pk],
                ),
            },
        }
