from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.audit.services import log_action
from ..services.lifecycle import ReferencedQuestionError
from ..services.question_groups import (
    QuestionGroupRevisionConflict, delete_question_group, project_question_group, save_question_group,
)
from .management_serializers import (
    ManagementDeleteConfirmationSerializer, ManagementErrorResponseSerializer,
    QuestionGroupRequestSerializer, QuestionGroupResponseSerializer,
)
from .management_views import (
    TeacherQuestionBankManagementApiView, _api_error_response, _standalone_bank_for_user,
)
from .views import _bank_for_course, _course_for_teacher


def _bank(request, bank_id, course_id):
    return _bank_for_course(_course_for_teacher(request.user, course_id), bank_id) if course_id is not None else _standalone_bank_for_user(request.user, bank_id)


@transaction.atomic
def _save(request, bank, group=None):
    serializer = QuestionGroupRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    if group is not None and 'expected_revision' not in serializer.validated_data:
        raise ValidationError({'expected_revision': '保存题组必须提供预期版本。'})
    try:
        group = save_question_group(bank=bank, data=serializer.validated_data, group=group)
    except QuestionGroupRevisionConflict as error:
        return _api_error_response('group_revision_conflict', str(error), status.HTTP_409_CONFLICT)
    except ReferencedQuestionError as error:
        return _api_error_response('question_referenced', str(error), status.HTTP_409_CONFLICT)
    except (DjangoValidationError, ValueError, TypeError) as error:
        raise ValidationError({'group': error.messages if isinstance(error, DjangoValidationError) else str(error)}) from error
    log_action(request, 'question_group.update' if request.method == 'PUT' else 'question_group.create', group,
        {'bank_id': bank.pk, 'revision': group.revision})
    return Response(project_question_group(group), status=status.HTTP_200_OK if request.method == 'PUT' else status.HTTP_201_CREATED)


class TeacherQuestionGroupCollectionView(TeacherQuestionBankManagementApiView):
    @extend_schema(request=None, responses=QuestionGroupResponseSerializer(many=True))
    def get(self, request, bank_id, course_id=None):
        bank = _bank(request, bank_id, course_id)
        return Response([project_question_group(group) for group in bank.groups.all()])

    @extend_schema(request=QuestionGroupRequestSerializer, responses={201: QuestionGroupResponseSerializer, 400: ManagementErrorResponseSerializer})
    def post(self, request, bank_id, course_id=None):
        return _save(request, _bank(request, bank_id, course_id))


class TeacherQuestionGroupDetailView(TeacherQuestionBankManagementApiView):
    @extend_schema(request=None, responses=QuestionGroupResponseSerializer)
    def get(self, request, bank_id, group_id, course_id=None):
        group = get_object_or_404(_bank(request, bank_id, course_id).groups, pk=group_id)
        return Response(project_question_group(group))

    @extend_schema(request=QuestionGroupRequestSerializer, responses={200: QuestionGroupResponseSerializer, 400: ManagementErrorResponseSerializer, 409: ManagementErrorResponseSerializer})
    def put(self, request, bank_id, group_id, course_id=None):
        bank = _bank(request, bank_id, course_id)
        return _save(request, bank, get_object_or_404(bank.groups, pk=group_id))

    @extend_schema(request=ManagementDeleteConfirmationSerializer, responses={204: None, 409: ManagementErrorResponseSerializer})
    @transaction.atomic
    def delete(self, request, bank_id, group_id, course_id=None):
        group = get_object_or_404(_bank(request, bank_id, course_id).groups, pk=group_id)
        if request.data.get('confirmation') != 'delete':
            raise ValidationError({'confirmation': '请确认永久删除整个题组。'})
        try:
            delete_question_group(group)
        except ReferencedQuestionError as error:
            return _api_error_response('question_referenced', str(error), status.HTTP_409_CONFLICT)
        log_action(request, 'question_group.delete', details={'group_id': group_id, 'bank_id': bank_id})
        return Response(status=status.HTTP_204_NO_CONTENT)
