from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema
from apps.api.permissions import RolePermission
from apps.accounts.models import User
from apps.accounts.course_access import get_accessible_course, require_course_owner
from apps.accounts.collaboration import collaboration_payload, mutate_collaboration, person


@extend_schema(exclude=True)
class CourseCollaborationView(APIView):
    permission_classes = [RolePermission]
    allowed_roles = (User.Role.TEACHER, User.Role.ADMIN)

    def get(self, request, course_id):
        return Response(collaboration_payload(get_accessible_course(request.user, course_id), request.user))

    def post(self, request, course_id):
        course = get_accessible_course(request.user, course_id)
        require_course_owner(course, request.user)
        try:
            teacher_id = int(request.data.get('teacher_id', 0))
        except (TypeError, ValueError):
            raise ValidationError('请选择教师账号。')
        try:
            course = mutate_collaboration(course, request.user, action=request.data.get('action'), teacher_id=teacher_id, permissions=request.data.get('permissions'), state_token=request.data.get('state_token', ''), request=request, keep_previous=request.data.get("keep_previous", True) is True, reason=request.data.get("reason", ""))
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages)
        return Response(collaboration_payload(course, request.user))


@extend_schema(exclude=True)
class CourseCollaborationTeachersView(CourseCollaborationView):
    http_method_names = ['get', 'head', 'options']

    def get(self, request, course_id):
        course = get_accessible_course(request.user, course_id)
        require_course_owner(course, request.user)
        query = request.query_params.get('q', '').strip()
        if not query:
            return Response({'items': []})
        teachers = User.objects.filter(role=User.Role.TEACHER, is_active=True).exclude(pk=course.owner_id)
        if query:
            teachers = teachers.filter(Q(username__icontains=query) | Q(display_name__icontains=query))
        return Response({'items': [person(teacher) for teacher in teachers.order_by('username')[:50]]})
