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
    _scope_from_request,
    _teaching_scope_display,
    _teaching_scope_from_request,
)
from apps.accounts.forms import ClassGroupForm
from apps.accounts.models import ClassGroup, Course, User
from apps.api.pagination import FixedPageNumberPagination
from apps.assessment.models import AssessmentEvent, ExperimentSession, SubmissionEvent
from apps.audit.services import log_action

from ..advanced_catalog import enabled_extension_tables
from ..advanced_materialized import (
    materialized_advanced_payload,
    materialized_student_search,
)
from ..data_scope import DataScope, ScopeNotFound, ScopeValidationError
from ..export_catalog import PACKAGE_TABLES
from ..models import ExportJob
from ..research_dataset import read_research_dataset, read_research_bundle, workspace_payload, context_tables, scope_label, session_options, table_catalog
from ..research_contract import file_catalog
from ..research_materialization_jobs import materialization_enabled_for_course
from ..quality_reports import EvaluationState, QualityReport, build_quality_report
from ..quality_materialized import materialized_quality_payload
from ..services import (
    ExportContractError,
    create_export_job,
    create_export_snapshot,
)
from ..teaching_scope import TeachingScope
from ..teaching.metrics import build_class_center_snapshot
from .serializers import (
    DataCenterAdvancedSerializer,
    DataCenterStudentSearchSerializer,
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
        "query": urlencode({key: ",".join(map(str, value)) if isinstance(value, list) else value for key, value in values.items()}),
        "display": scope_label(scope),
        "is_single_version": scope.is_single_version,
        "is_complete": scope.is_complete,
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
    filters = job.snapshot.filter_definition or {}
    class_name = ClassGroup.objects.filter(pk=filters.get("class_id")).values_list("name", flat=True).first() if filters.get("class_id") else None
    experiment_name = job.experiment.name if job.experiment else None
    if not experiment_name and filters.get("experiment_ids"):
        experiment_name = "、".join(job.course.experiments.filter(pk__in=filters["experiment_ids"]).order_by("pk").values_list("name", flat=True))
    return {
        "job_id": job.job_id,
        "package": job.package,
        "package_label": {"experiment": "实验数据包", "teaching": "教学成绩", "complete": "历史完整研究包", "process": "历史作答过程包", "kt": "历史知识追踪包", "cd": "历史认知诊断包", "confidence": "历史信心数据包", "simulation": "历史仿真数据包"}.get(job.package, "历史数据包"),
        "class_name": class_name or "班级未记录",
        "experiment_name": experiment_name,
        "status": job.status,
        "status_label": job.get_status_display(),
        "created_at": job.created_at,
    }


def _scoped_export_jobs(course, scope):
    jobs = course.export_jobs.select_related("experiment", "created_by", "snapshot")
    if scope.experiment_id:
        jobs = jobs.filter(experiment_id=scope.experiment_id)
    for key in ("class_id", "class_snapshot_id", "student_id", "session_id", "search", "session_ref"):
        value = getattr(scope, key, None)
        if value is not None:
            jobs = jobs.filter(**{f"snapshot__filter_definition__{key}": str(value) if key == "class_snapshot_id" else value})
    return jobs.order_by("-created_at", "-job_id")


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
            scope = (DataScope if "experiment_ids" in serializer.validated_data else TeachingScope).from_mapping(course, serializer.validated_data)
        except ScopeNotFound as error:
            raise NotFound(str(error)) from error
        except ScopeValidationError as error:
            raise ValidationError({"scope": [str(error)]}) from error
        if isinstance(scope, DataScope):
            request.session[f"data_center_scope_v1:{course.pk}"] = scope.serialize()
        else:
            request.session[f"data_center_teaching_scope_v1:{course.pk}"] = scope.serialize()
            request.session.pop(f"data_center_scope_v1:{course.pk}", None)
        request.session.modified = True
        return Response(self._payload(request, course, scope=scope))

    @staticmethod
    def _payload(request, course, *, scope=None):
        scope = scope or _scope_from_request(request, course)
        experiments = list(
            course.experiments.prefetch_related("versions").order_by("name", "pk")
        )
        classes = course.classes.filter(is_active=True).order_by(
            "academic_year", "name", "pk"
        )
        return {
                "course": _course_payload(course),
                "current": {"experiment_id": scope.experiment_id, "experiment_ids": list(getattr(scope, "experiment_ids", ())) or ([scope.experiment_id] if scope.experiment_id else []),
                    "class_id": scope.class_id, "is_complete": scope.is_complete,
                    "display": scope_label(scope) if isinstance(scope, DataScope) else _teaching_scope_display(scope)},
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
    history_query = urlencode({key: ",".join(map(str, value)) if isinstance(value, list) else value
        for key, value in (job.snapshot.filter_definition or {}).items()})
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
        "history_url": f"{reverse('courses:data_export_history', args=[job.course_id])}?{history_query}",
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
        package = "experiment" if values["package"] == "complete" else values["package"]
        if package not in {*PACKAGE_TABLES, "experiment"} or package == "identity":
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
        except ExportContractError as error:
            if "job" in locals():
                ExportJob.objects.filter(pk=job.pk, status__in=[ExportJob.Status.RUNNING, ExportJob.Status.QUEUED]).update(
                    status=ExportJob.Status.FAILED, progress_stage="contract_error", finished_at=timezone.now())
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
            OpenApiParameter("experiment_ids", str, required=False),
            OpenApiParameter("class_id", int, required=False),
        ],
        responses=DataCenterOverviewSerializer,
    )
    def get(self, request, course_id):
        course = _course_for_teacher(request.user, course_id)
        research_scope = _scope_from_request(request, course)
        class_id = research_scope.class_id
        if class_id is None and research_scope.class_snapshot_id:
            class_id = research_scope._course_class_snapshot(research_scope.class_snapshot_id).source_class_group_id
        # The research scope validates multi-experiment selections; this adapter
        # only supplies the existing class-membership roster projection.
        scope = TeachingScope(course=course, experiment_id=research_scope.experiment_id, class_id=class_id)
        sessions = research_scope.sessions() if research_scope.is_complete else scope.sessions().none()
        attempts = research_scope.attempts() if research_scope.is_complete else scope.attempts().none()
        report = build_quality_report(research_scope) if research_scope.is_complete else QualityReport(())
        warning_attempt_ids = {
            row.attempt_id
            for row in report.rows
            if row.attempt_id is not None
            and row.state in {EvaluationState.WARN, EvaluationState.FAIL}
        }
        recent_jobs = _scoped_export_jobs(course, research_scope)[:5] if research_scope.is_complete else ()
        return Response(
            {
                "course": _course_payload(course),
                "scope": {"experiment_id": research_scope.experiment_id, "class_id": class_id,
                          "is_complete": research_scope.is_complete, "display": scope_label(research_scope)},
                "facts": {
                    "participants": sessions.values("student_id").distinct().count() if research_scope.search or research_scope.session_ref else scope.roster().count(),
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
                    "process_records": AssessmentEvent.objects.filter(session__in=sessions).count(),
                    "not_evaluable": report.counts.get(
                        EvaluationState.NOT_EVALUABLE, 0
                    ),
                },
                "recent_exports": [
                    _recent_export_payload(job) for job in recent_jobs
                ],
                "actions": {
                    "preview_url": f"{reverse('courses:data_advanced', args=[course.pk])}?{_research_scope_payload(research_scope)["query"]}",
                    "scope_update_url": reverse(
                        "teacher-data-center-scope", args=[course.pk]
                    ),
                    "governance_url": f"{reverse('courses:settings', args=[course.pk])}#identity-governance",
                    "teaching_url": reverse(
                        "courses:data_teaching", args=[course.pk]
                    ),
                    "exports_url": f"{reverse('courses:data_exports', args=[course.pk])}?{_research_scope_payload(research_scope)["query"]}",
                    "quality_url": f"{reverse('courses:data_quality', args=[course.pk])}?{_research_scope_payload(research_scope)["query"]}",
                    "history_url": f"{reverse('courses:data_export_history', args=[course.pk])}?{_research_scope_payload(research_scope)["query"]}",
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
        package_rows = [{"key": "experiment", "label": "实验数据包", "description": "七张 CSV 与课程题目固定库 JSON", "blocked": not scope.is_complete}]
        selected_package = package_rows[0]
        bundle, generated_at = read_research_bundle(scope) if scope.is_complete else (None, timezone.now())
        return Response(
            {
                "course": _course_payload(course),
                "scope": _research_scope_payload(scope),
                "packages": package_rows,
                "selected_package": selected_package,
                "export_versions": [],
                "selected_export_version": None,
                "tables": file_catalog(bundle["files"] if bundle else None),
                "generated_at": generated_at,
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
        if materialization_enabled_for_course(course.pk):
            payload = materialized_quality_payload(scope)
            paginator = FixedPageNumberPagination()
            page = paginator.paginate_queryset(payload["items"], request, view=self)
            response = paginator.get_paginated_response(page)
            response.data.update(
                {
                    "course": _course_payload(course),
                    "scope": _research_scope_payload(scope),
                    "counts": payload["counts"],
                    "preparation": payload["preparation"],
                }
            )
            return response
        report = build_quality_report(scope) if scope.is_complete else None
        state_labels = {"PASS": "记录正常", "WARN": "需要注意", "FAIL": "记录不一致", "NOT_EVALUABLE": "无法评估", "NOT_APPLICABLE": "不适用"}
        explanations = {
            "missing_native_event_anchor": ("缺少原生行为记录", "无法核验行为与题目的对应关系"),
            "identity_anchor_mismatch": ("行为记录与题目对应关系不一致", "不能可靠关联该行为"),
            "missing_native_state_observation": ("缺少原生答案状态记录", "不能完整还原答案变化"),
            "no_response_state_event": ("没有答案变化记录", "不适用答案状态链检查"),
            "response_state_chain_mismatch": ("答案变化记录不连续或不一致", "不能完整还原答案变化"),
            "attempt_not_submitted": ("尚未正式提交", "没有可供核验的正式最终答案"),
            "missing_submission": ("作答显示已提交但缺少正式提交记录", "无法确认最终答案"),
            "submission_snapshot_mismatch": ("正式提交与答案记录不一致", "最终答案需要核查"),
            "missing_client_sequence": ("缺少客户端顺序记录", "不能精确判断学生操作顺序和间隔"),
            "client_sequence_not_strict": ("客户端顺序记录存在冲突", "操作先后顺序需要核查"),
            "interval_degraded": ("时间记录精度不足", "不能按精确时长解释操作间隔"),
            "export_scoped_rule": ("本项在生成数据包时检查", "实时预览中不作判断"),
        }
        refs = scope.session_references() if scope.is_complete else {}
        attempts = {item.pk: item for item in scope.attempts().select_related("session__student", "assignment")} if scope.is_complete else {}
        dataset, _ = read_research_dataset(scope) if scope.is_complete else (None, None)
        question_refs = {(row[0], row[4]): row[3] for row in dataset["responses"][1]} if dataset else {}
        items = []
        for row in report.rows if report else ():
            attempt = attempts.get(row.attempt_id)
            reference = refs.get(str(attempt.session_id), "") if attempt else ""
            problem, impact = explanations.get(row.reason_code, ("本项记录核验正常", "未发现影响"))
            items.append({
                "student_number": attempt.session.student.student_number or "" if attempt else "",
                "student_name": attempt.session.student.shown_name if attempt else "",
                "session_ref": reference,
                "question_code": question_refs.get((reference, str(attempt.assignment.assigned_sequence_index)), "") if attempt else "",
                "state": str(row.state), "state_label": state_labels[str(row.state)], "problem": problem, "impact": impact,
            })
        paginator = FixedPageNumberPagination()
        page = paginator.paginate_queryset(items, request, view=self)
        response = paginator.get_paginated_response(page)
        response.data.update({"course": _course_payload(course), "scope": _research_scope_payload(scope),
            "counts": {label: report.counts.get(state, 0) if report else 0 for state, label in state_labels.items()}})
        return response



class TeacherDataCenterExportHistoryView(APIView):
    @extend_schema(
        operation_id="teacher_data_center_export_history_list",
        responses=DataCenterExportHistoryCollectionSerializer,
    )
    def get(self, request, course_id):
        course = _course_for_teacher(request.user, course_id)
        scope = _scope_from_request(request, course)
        jobs = _scoped_export_jobs(course, scope)
        paginator = FixedPageNumberPagination()
        page = paginator.paginate_queryset(jobs, request, view=self)
        items = []
        for job in page:
            can_manage_artifact = job.status == ExportJob.Status.SUCCEEDED
            items.append(
                {
                    **_recent_export_payload(job),
                    "artifact_removal_reason": job.artifact_removal_reason,
                    "artifact_removal_label": {"user_requested_removal": "已由用户移除文件", "retention_expired": "已超过保留期限"}.get(job.artifact_removal_reason, "文件已移除" if job.artifact_removal_reason else ""),
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
        response.data["scope"] = _research_scope_payload(scope)
        return response


class TeacherDataCenterAdvancedView(APIView):
    @extend_schema(
        operation_id="teacher_data_center_advanced_retrieve",
        parameters=[
            OpenApiParameter("experiment_id", int, required=False),
            OpenApiParameter("class_id", int, required=False),
            OpenApiParameter("table", str, required=False),
            OpenApiParameter("search", str, required=False),
            OpenApiParameter("session_ref", str, required=False),
            OpenApiParameter("page", int, required=False),
        ],
        responses=DataCenterAdvancedSerializer,
    )
    def get(self, request, course_id):
        course = _course_for_teacher(request.user, course_id)
        scope = _scope_from_request(request, course)
        if materialization_enabled_for_course(course.pk):
            payload = materialized_advanced_payload(course, scope, request.query_params)
            return Response(DataCenterAdvancedSerializer(payload).data)
        selected_key = request.query_params.get("table", "sessions" if request.query_params.get("view", "overview") == "overview" else "responses")
        if selected_key == "attempts":
            selected_key = "responses"
        view = request.query_params.get("view") if request.query_params.get("view") in {"overview", "student", "session", "question", "timeline"} else "overview"
        from dataclasses import replace
        read_scope = replace(scope, session_id=None, session_ref=None) if view == "student" else scope
        bundle, generated_at = read_research_bundle(read_scope) if scope.is_complete else (None, timezone.now())
        workspace = workspace_payload(bundle, scope, request.query_params)
        dataset = context_tables(bundle, workspace, view) if bundle else None
        tables = table_catalog(dataset)
        selected = next((item for item in tables if item["key"] == selected_key), tables[4])
        preview = None
        if dataset is not None:
            headers, rows = dataset[selected["key"]]
            total = len(rows)
            total_pages = max(1, ceil(total / 20))
            try:
                page = max(1, min(int(request.query_params.get("page", 1)), total_pages))
            except (ValueError, TypeError):
                page = 1
            offset = (page - 1) * 20
            preview = {
                "columns": [{"name": header, "description": header} for header in headers],
                "rows": rows[offset:offset + 20],
                "page": {"number": page, "size": 20, "total_items": total, "total_pages": total_pages,
                         "start": offset + 1 if total else 0, "end": min(offset + 20, total)},
                "data_status": "observed" if total else "no_observation",
            }
        experiment = course.experiments.filter(pk=scope.experiment_id).first() if scope.experiment_id else None
        payload = {
            "course": _course_payload(course), "scope": _research_scope_payload(scope),
            "search": scope.search or "", "tables": tables, "selected_table": selected,
            "selected_experiment": {"id": experiment.pk, "name": experiment.name, "versions": []} if experiment else None,
            "session_options": session_options(scope) if scope.is_complete else [],
            "generated_at": generated_at, "preview": preview,
            "view": view,
            "workspace": workspace,
        }
        return Response(DataCenterAdvancedSerializer(payload).data)


class TeacherDataCenterAdvancedStudentSearchView(APIView):
    @extend_schema(
        operation_id="teacher_data_center_advanced_students_list",
        parameters=[
            OpenApiParameter("experiment_id", int, required=False),
            OpenApiParameter("experiment_ids", str, required=False),
            OpenApiParameter("class_id", int, required=False),
            OpenApiParameter("q", str, required=False),
        ],
        responses=DataCenterStudentSearchSerializer,
    )
    def get(self, request, course_id):
        course = _course_for_teacher(request.user, course_id)
        scope = _scope_from_request(request, course)
        payload = materialized_student_search(course, scope, request.query_params.get("q"))
        return Response(DataCenterStudentSearchSerializer(payload).data)



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
            "can_create": can_create_course_class_group(course, request.user),
            "can_manage": can_create_course_class_group(course, request.user),
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
