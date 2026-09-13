from django.core.exceptions import PermissionDenied, ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from django.http import QueryDict
from django.shortcuts import get_object_or_404
from django.urls import reverse
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.course_access import accessible_courses, lock_writable_course
from apps.accounts.models import User
from apps.analytics.export_fields import FIELD_GROUPS
from apps.api.exceptions import ApiConflict
from apps.api.permissions import RolePermission
from apps.assessment.models import ExperimentSession
from apps.audit.services import log_action
from apps.question_bank.models import Question, QuestionBank

from ..forms import ExperimentForm, StudentAssignmentForm
from ..models import Experiment
from ..services import (
    assign_students,
    delete_experiment as delete_experiment_tree,
    lock_experiment_question_bank,
    publish_experiment,
    selection_display_context,
    sync_experiment_questions,
)
from .standalone_serializers import (
    StandaloneActionInputSerializer,
    StandaloneExperimentCollectionSerializer,
    StandaloneExperimentDetailSerializer,
    StandaloneExperimentInputSerializer,
    StandaloneExperimentResultsSerializer,
    StandaloneStudentAssignmentInputSerializer,
)


FORM_FIELDS = (
    "name",
    "description",
    "ordering_mode",
    "feedback_mode",
    "feedback_release_policy",
    "time_mode",
    "total_time_limit_minutes",
    "per_question_time_limit_seconds",
    "resume_mode",
    "confidence_mode",
    "confidence_sampling_unit",
    "confidence_probability",
    "confidence_randomization_seed",
    "confidence_assignment_version",
    "confidence_scale_version",
    "confidence_prompt_version",
    "heartbeat_interval_seconds",
)


def _form_error_payload(form):
    errors = {}
    for field_name, messages in form.errors.items():
        api_name = "question_bank_id" if field_name == "question_bank" else field_name
        errors[api_name] = [str(message) for message in messages]
    return errors


def _raise_domain_error(error):
    if isinstance(error, PermissionDenied):
        raise NotFound() from error
    messages = getattr(error, "messages", None)
    if messages:
        raise ValidationError({"non_field_errors": messages}) from error
    raise error


def _choice_rows(choices):
    return [{"value": value, "label": label} for value, label in choices]


def _question_bank_choices(user):
    if user.is_superuser or user.role == User.Role.ADMIN:
        queryset = QuestionBank.objects.exclude(visibility=QuestionBank.Visibility.ARCHIVED)
    else:
        queryset = user.question_banks.exclude(visibility=QuestionBank.Visibility.ARCHIVED)
    return list(queryset.order_by("name", "pk").values("id", "name"))


def _choice_payload():
    return {
        "selection_modes": _choice_rows(Experiment.SelectionMode.choices),
        "ordering_modes": _choice_rows(Experiment.Ordering.choices),
        "feedback_modes": _choice_rows(Experiment.Feedback.choices),
        "feedback_release_policies": _choice_rows(
            Experiment.FeedbackReleasePolicy.choices
        ),
        "time_modes": _choice_rows(Experiment.TimeMode.choices),
        "resume_modes": _choice_rows(Experiment.ResumeMode.choices),
        "confidence_modes": _choice_rows(Experiment.ConfidenceMode.choices),
        "confidence_sampling_units": _choice_rows(
            Experiment.ConfidenceSamplingUnit.choices
        ),
        "question_types": _choice_rows(Question.Type.choices),
    }


def _detail_url(experiment, destination):
    if destination == "edit":
        return reverse("courses:experiment_edit", args=[experiment.course_id, experiment.pk])
    if destination == "results":
        return reverse("courses:experiment_results", args=[experiment.course_id, experiment.pk])
    return reverse("courses:experiment_detail", args=[experiment.course_id, experiment.pk])


def _canonical_payload(experiment, destination):
    return {"kind": "canonical", "canonical_url": _detail_url(experiment, destination)}


def _form_value(value):
    return "" if value is None else value


def _experiment_form_data(validated_data, *, instance=None):
    data = {}
    if instance is not None:
        data.update(
            {
                field_name: _form_value(getattr(instance, field_name))
                for field_name in FORM_FIELDS
            }
        )
        data["selection_mode"] = instance.selection_mode
        data["question_bank"] = instance.question_bank_id
        quota_map = dict(
            instance.question_quotas.values_list("question_type", "amount")
        )
    else:
        quota_map = {}

    for field_name in FORM_FIELDS:
        if field_name in validated_data:
            data[field_name] = _form_value(validated_data[field_name])
    if "selection_mode" in validated_data:
        data["selection_mode"] = validated_data["selection_mode"]
    if "question_bank_id" in validated_data:
        data["question_bank"] = validated_data["question_bank_id"]
    elif instance is None:
        data["question_bank"] = ""

    if "question_quotas" in validated_data:
        quota_map = {
            row["question_type"]: row["amount"]
            for row in validated_data["question_quotas"]
        }
    for question_type in Question.Type.values:
        data[f"quota_{question_type}"] = quota_map.get(question_type, 0)
    return data


def _experiment_projection(experiment):
    return {
        "id": experiment.pk,
        "name": experiment.name,
        "description": experiment.description,
        "question_bank": {
            "id": experiment.question_bank_id,
            "name": experiment.question_bank.name,
        },
        "status": experiment.status,
        "status_label": experiment.get_status_display(),
        "selection_mode": experiment.selection_mode,
        "selection_mode_label": experiment.get_selection_mode_display(),
        "ordering_mode": experiment.ordering_mode,
        "ordering_mode_label": experiment.get_ordering_mode_display(),
        "feedback_mode": experiment.feedback_mode,
        "feedback_mode_label": experiment.get_feedback_mode_display(),
        "feedback_release_policy": experiment.feedback_release_policy,
        "feedback_release_policy_label": experiment.get_feedback_release_policy_display(),
        "time_mode": experiment.time_mode,
        "time_mode_label": experiment.get_time_mode_display(),
        "total_time_limit_minutes": experiment.total_time_limit_minutes,
        "per_question_time_limit_seconds": experiment.per_question_time_limit_seconds,
        "resume_mode": experiment.resume_mode,
        "resume_mode_label": experiment.get_resume_mode_display(),
        "confidence_mode": experiment.confidence_mode,
        "confidence_mode_label": experiment.get_confidence_mode_display(),
        "confidence_sampling_unit": experiment.confidence_sampling_unit,
        "confidence_sampling_unit_label": experiment.get_confidence_sampling_unit_display(),
        "confidence_probability": experiment.confidence_probability,
        "confidence_randomization_seed": experiment.confidence_randomization_seed,
        "confidence_assignment_version": experiment.confidence_assignment_version,
        "confidence_scale_version": experiment.confidence_scale_version,
        "confidence_prompt_version": experiment.confidence_prompt_version,
        "heartbeat_interval_seconds": experiment.heartbeat_interval_seconds,
        "question_count": experiment.question_links.count(),
        "student_count": experiment.student_links.count(),
        "created_at": experiment.created_at,
        "updated_at": experiment.updated_at,
    }


def _selection_projection(experiment):
    context = selection_display_context(experiment)
    return {
        "quota_rows": context["quota_rows"],
        "current_candidate_count": context["current_candidate_count"],
        "current_inventory_rows": context["current_inventory_rows"],
        "version_rows": [
            {
                "version_number": row["version"].version_number,
                "published_at": row["version"].published_at,
                "candidate_count": row["candidate_count"],
                "inventory_rows": row["inventory_rows"],
            }
            for row in context["version_rows"]
        ],
    }


def _student_projection(student, *, selected):
    return {
        "id": student.pk,
        "student_number": student.student_number,
        "display_name": student.shown_name,
        "class_name": str(student.class_group or ""),
        "selected": selected,
    }


def _detail_projection(experiment, user):
    selected_students = list(
        experiment.students.select_related("class_group").order_by("student_number", "pk")
    )
    selected_ids = {student.pk for student in selected_students}
    available_students = []
    if experiment.status == Experiment.Status.DRAFT:
        assignment_form = StudentAssignmentForm(owner=user)
        available_students = [
            _student_projection(student, selected=student.pk in selected_ids)
            for student in assignment_form.fields["students"].queryset.select_related(
                "class_group"
            ).order_by("student_number", "pk")
        ]
    api_base = f"/api/v1/teacher/standalone-experiments/{experiment.pk}/"
    return {
        "kind": "standalone",
        "experiment": _experiment_projection(experiment),
        "selection": _selection_projection(experiment),
        "students": [
            _student_projection(student, selected=True) for student in selected_students
        ],
        "available_students": available_students,
        "question_banks": _question_bank_choices(user),
        "actions": {
            "detail_url": reverse("experiments:detail", args=[experiment.pk]),
            "edit_url": reverse("experiments:edit", args=[experiment.pk]),
            "results_url": reverse("experiments:results", args=[experiment.pk]),
            "assign_url": f"{api_base}students/",
            "publish_url": f"{api_base}publish/",
            "sync_questions_url": f"{api_base}sync-questions/",
            "delete_url": api_base,
            "can_edit": experiment.status == Experiment.Status.DRAFT,
            "can_assign": experiment.status == Experiment.Status.DRAFT,
            "can_publish": experiment.status == Experiment.Status.DRAFT,
            "can_sync_questions": experiment.status == Experiment.Status.DRAFT,
        },
        "choices": _choice_payload(),
    }


class TeacherStandaloneExperimentApiMixin:
    permission_classes = (RolePermission,)
    allowed_roles = (User.Role.TEACHER,)

    def get_owned_experiment(self, *, lock=False):
        queryset = Experiment.objects.all()
        user = self.request.user
        if not (user.is_superuser or user.role == User.Role.ADMIN):
            queryset = queryset.filter(
                Q(owner=user) | Q(course__in=accessible_courses(user))
            )
        queryset = queryset.select_related("question_bank", "course")
        if lock:
            queryset = queryset.select_for_update(of=("self",))
        return get_object_or_404(queryset, pk=self.kwargs["experiment_id"])

    def get_standalone_experiment(self, *, lock=False):
        experiment = self.get_owned_experiment(lock=lock)
        if experiment.course_id:
            raise NotFound()
        return experiment


class TeacherStandaloneExperimentCollectionView(
    TeacherStandaloneExperimentApiMixin, APIView
):
    @extend_schema(
        operation_id="teacher_standalone_experiment_list",
        responses=StandaloneExperimentCollectionSerializer,
    )
    def get(self, request):
        if accessible_courses(request.user).exists():
            return Response(
                {"kind": "canonical", "canonical_url": reverse("courses:list")}
            )
        experiments = (
            Experiment.objects.filter(owner=request.user, course__isnull=True)
            .select_related("question_bank")
            .prefetch_related("student_links")
        )
        return Response(
            {
                "kind": "standalone",
                "items": [
                    {
                        "id": experiment.pk,
                        "name": experiment.name,
                        "description": experiment.description,
                        "question_bank": {
                            "id": experiment.question_bank_id,
                            "name": experiment.question_bank.name,
                        },
                        "ordering_mode_label": experiment.get_ordering_mode_display(),
                        "student_count": experiment.student_links.count(),
                        "status": experiment.status,
                        "status_label": experiment.get_status_display(),
                        "updated_at": experiment.updated_at,
                        "detail_url": reverse("experiments:detail", args=[experiment.pk]),
                    }
                    for experiment in experiments
                ],
                "question_banks": _question_bank_choices(request.user),
                "choices": _choice_payload(),
            }
        )

    @extend_schema(
        operation_id="teacher_standalone_experiment_create",
        request=StandaloneExperimentInputSerializer,
        responses={201: StandaloneExperimentDetailSerializer},
    )
    def post(self, request):
        input_serializer = StandaloneExperimentInputSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        form = ExperimentForm(
            _experiment_form_data(input_serializer.validated_data),
            owner=request.user,
        )
        if not form.is_valid():
            raise ValidationError(_form_error_payload(form))
        courses = accessible_courses(request.user).filter(status="active")
        try:
            with transaction.atomic():
                experiment = form.save(commit=False)
                if experiment.question_bank.course_id:
                    course = lock_writable_course(experiment.question_bank.course_id)
                    if not courses.filter(pk=course.pk).exists():
                        raise DjangoValidationError("题库不属于可管理的课程")
                    question_bank = lock_experiment_question_bank(
                        form.cleaned_data["question_bank"], course
                    )
                    experiment.course = course
                    experiment.created_by = request.user
                else:
                    question_bank = lock_experiment_question_bank(
                        form.cleaned_data["question_bank"]
                    )
                    experiment.cross_experiment_item_policy = (
                        Experiment.CrossExperimentItemPolicy.UNRESTRICTED
                    )
                experiment.question_bank = question_bank
                experiment.owner = request.user
                experiment.save()
                form.save_quotas(experiment)
                sync_experiment_questions(experiment)
                log_action(request, "experiment.create", experiment, {"surface": "api"})
        except DjangoValidationError as error:
            _raise_domain_error(error)

        experiment = Experiment.objects.select_related("question_bank", "course").get(
            pk=experiment.pk
        )
        if experiment.course_id:
            return Response(
                _canonical_payload(experiment, "detail"),
                status=201,
            )
        return Response(_detail_projection(experiment, request.user), status=201)


class TeacherStandaloneExperimentDetailView(
    TeacherStandaloneExperimentApiMixin, APIView
):
    @extend_schema(
        operation_id="teacher_standalone_experiment_retrieve",
        responses=StandaloneExperimentDetailSerializer,
    )
    def get(self, request, experiment_id):
        experiment = self.get_owned_experiment()
        destination = request.query_params.get("destination", "detail")
        if destination not in {"detail", "edit"}:
            raise ValidationError({"destination": "仅支持 detail 或 edit。"})
        if experiment.course_id:
            return Response(_canonical_payload(experiment, destination))
        return Response(_detail_projection(experiment, request.user))

    @extend_schema(
        operation_id="teacher_standalone_experiment_update",
        request=StandaloneExperimentInputSerializer,
        responses=StandaloneExperimentDetailSerializer,
    )
    def patch(self, request, experiment_id):
        input_serializer = StandaloneExperimentInputSerializer(data=request.data, partial=True)
        input_serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                experiment = self.get_standalone_experiment(lock=True)
                if experiment.status != Experiment.Status.DRAFT:
                    raise ApiConflict(
                        "实验定义已冻结，不能再修改。",
                        code="EXPERIMENT_FROZEN",
                    )
                form = ExperimentForm(
                    _experiment_form_data(
                        input_serializer.validated_data,
                        instance=experiment,
                    ),
                    instance=experiment,
                    owner=request.user,
                )
                if not form.is_valid():
                    raise ValidationError(_form_error_payload(form))
                original_bank_id = experiment.question_bank_id
                question_bank = lock_experiment_question_bank(
                    form.cleaned_data["question_bank"]
                )
                experiment = form.save(commit=False)
                experiment.question_bank = question_bank
                experiment.save()
                form.save_quotas(experiment)
                if experiment.question_bank_id != original_bank_id:
                    sync_experiment_questions(experiment)
                log_action(request, "experiment.update", experiment, {"surface": "api"})
        except DjangoValidationError as error:
            _raise_domain_error(error)
        experiment = Experiment.objects.select_related("question_bank", "course").get(
            pk=experiment.pk
        )
        return Response(_detail_projection(experiment, request.user))

    @extend_schema(
        operation_id="teacher_standalone_experiment_delete",
        request=StandaloneActionInputSerializer,
        responses={204: None},
    )
    def delete(self, request, experiment_id):
        input_serializer = StandaloneActionInputSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        if input_serializer.validated_data.get("confirmation") != "delete":
            raise ValidationError({"confirmation": "请在二次确认后删除实验。"})
        with transaction.atomic():
            experiment = self.get_standalone_experiment(lock=True)
            details = delete_experiment_tree(experiment)
            log_action(request, "experiment.delete", details=details)
        return Response(status=204)


class TeacherStandaloneExperimentStudentsView(
    TeacherStandaloneExperimentApiMixin, APIView
):
    @extend_schema(
        operation_id="teacher_standalone_experiment_assign_students",
        request=StandaloneStudentAssignmentInputSerializer,
        responses=StandaloneExperimentDetailSerializer,
    )
    def post(self, request, experiment_id):
        input_serializer = StandaloneStudentAssignmentInputSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        student_ids = input_serializer.validated_data["student_ids"]
        form_data = QueryDict("", mutable=True)
        form_data.setlist("students", [str(student_id) for student_id in student_ids])
        assignment_form = StudentAssignmentForm(form_data, owner=request.user)
        if not assignment_form.is_valid():
            raise ValidationError(_form_error_payload(assignment_form))
        try:
            experiment = self.get_standalone_experiment()
            assign_students(experiment, assignment_form.cleaned_data["students"])
            log_action(
                request,
                "experiment.assign_students",
                experiment,
                {"count": len(assignment_form.cleaned_data["students"]), "surface": "api"},
            )
        except DjangoValidationError as error:
            _raise_domain_error(error)
        experiment = Experiment.objects.select_related("question_bank", "course").get(
            pk=experiment.pk
        )
        return Response(_detail_projection(experiment, request.user))


class TeacherStandaloneExperimentPublishView(
    TeacherStandaloneExperimentApiMixin, APIView
):
    @extend_schema(
        operation_id="teacher_standalone_experiment_publish",
        request=None,
        responses=StandaloneExperimentDetailSerializer,
    )
    def post(self, request, experiment_id):
        try:
            experiment = self.get_standalone_experiment()
            publish_experiment(experiment, request.user)
            log_action(request, "experiment.publish", experiment, {"surface": "api"})
        except DjangoValidationError as error:
            _raise_domain_error(error)
        experiment = Experiment.objects.select_related("question_bank", "course").get(
            pk=experiment.pk
        )
        return Response(_detail_projection(experiment, request.user))


class TeacherStandaloneExperimentSyncQuestionsView(
    TeacherStandaloneExperimentApiMixin, APIView
):
    @extend_schema(
        operation_id="teacher_standalone_experiment_sync_questions",
        request=None,
        responses=StandaloneExperimentDetailSerializer,
    )
    def post(self, request, experiment_id):
        try:
            experiment = self.get_standalone_experiment()
            sync_experiment_questions(experiment)
            log_action(
                request,
                "experiment.sync_questions",
                experiment,
                {"count": experiment.question_links.count(), "surface": "api"},
            )
        except DjangoValidationError as error:
            _raise_domain_error(error)
        experiment = Experiment.objects.select_related("question_bank", "course").get(
            pk=experiment.pk
        )
        return Response(_detail_projection(experiment, request.user))


class TeacherStandaloneExperimentResultsView(
    TeacherStandaloneExperimentApiMixin, APIView
):
    @extend_schema(
        operation_id="teacher_standalone_experiment_results",
        responses=StandaloneExperimentResultsSerializer,
    )
    def get(self, request, experiment_id):
        experiment = self.get_owned_experiment()
        if experiment.course_id:
            return Response(_canonical_payload(experiment, "results"))
        latest_version = experiment.versions.order_by("-version_number").first()
        sessions = (
            latest_version.sessions.select_related("student")
            if latest_version
            else ExperimentSession.objects.none()
        )
        valid_statuses = {value for value, _label in ExperimentSession.Status.choices}
        selected_status = request.query_params.get("status", "")
        if selected_status in valid_statuses:
            sessions = sessions.filter(status=selected_status)
        else:
            selected_status = ""
        return Response(
            {
                "kind": "standalone",
                "experiment": _experiment_projection(experiment),
                "sessions": [
                    {
                        "id": session.pk,
                        "student_number": session.student.student_number,
                        "student_name": session.student.shown_name,
                        "status": session.status,
                        "status_label": session.get_status_display(),
                        "started_at": session.started_at,
                        "submission_count": session.submission_count,
                    }
                    for session in sessions
                ],
                "session_status_choices": _choice_rows(ExperimentSession.Status.choices),
                "selected_status": selected_status,
                "export_field_groups": [
                    {
                        "label": label,
                        "fields": [
                            {"key": key, "label": field_label}
                            for key, field_label in fields
                        ],
                    }
                    for label, fields in FIELD_GROUPS
                ],
                "actions": {
                    "identified_export_url": reverse(
                        "analytics:identified_export", args=[experiment.pk]
                    ),
                    "identified_navigation_export_url": reverse(
                        "analytics:identified_navigation_export", args=[experiment.pk]
                    ),
                    "kt_export_url": reverse("analytics:kt_export", args=[experiment.pk]),
                    "navigation_export_url": reverse(
                        "analytics:navigation_export", args=[experiment.pk]
                    ),
                    "identity_export_url": reverse(
                        "analytics:identity_export", args=[experiment.pk]
                    ),
                    "detail_url": reverse("experiments:detail", args=[experiment.pk]),
                },
            }
        )
