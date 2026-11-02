from django.db.models import Q
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import ClassGroup, Course, User
from apps.api.permissions import RolePermission


class AdminChoiceQuery(serializers.Serializer):
    q = serializers.CharField(required=False, allow_blank=True, max_length=200, default='')
    page = serializers.IntegerField(min_value=1, max_value=2147483647, default=1)
    course_id = serializers.IntegerField(min_value=1, required=False)
    exclude_id = serializers.IntegerField(min_value=1, required=False)
    selected_id = serializers.IntegerField(min_value=1, required=False)
    include_archived = serializers.ChoiceField(choices=('0', '1'), default='0')


def _option(kind, row):
    if kind == 'courses':
        description = ' · '.join(filter(None, (row['code'], row['term'], row['owner__display_name'] or row['owner__username'])))
        return {'value': row['id'], 'label': row['name'], 'description': description}
    if kind == 'classes':
        return {'value': row['id'], 'label': row['name'], 'description': row['academic_year']}
    return {'value': row['id'], 'label': row['display_name'] or row['username'], 'description': row['username']}


class AdminChoiceView(APIView):
    permission_classes = (RolePermission,)
    allowed_roles = ()

    @extend_schema(parameters=[AdminChoiceQuery], responses=dict)
    def get(self, request, kind):
        if kind not in ('courses', 'classes', 'teachers'):
            raise ValidationError({'kind': '请选择课程、班级或教师。'})
        serializer = AdminChoiceQuery(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        if kind != 'courses' and values['include_archived'] == '1':
            raise ValidationError({'include_archived': '只有课程筛选支持归档课程。'})

        if kind == 'courses':
            queryset = Course.objects.filter(deleted_at__isnull=True)
            if values['include_archived'] != '1':
                queryset = queryset.filter(status=Course.Status.ACTIVE)
            queryset = queryset.order_by('name', 'pk').values('id', 'name', 'code', 'term', 'owner__display_name', 'owner__username')
            search = Q(name__icontains=values['q']) | Q(code__icontains=values['q']) | Q(term__icontains=values['q']) | Q(owner__display_name__icontains=values['q'])
        elif kind == 'classes':
            course_id = values.get('course_id')
            if not course_id or not Course.objects.filter(pk=course_id, status=Course.Status.ACTIVE).exists():
                raise ValidationError({'course_id': '请先选择有效且启用中的课程。'})
            queryset = ClassGroup.objects.filter(course_id=course_id, course__deleted_at__isnull=True,
                course__status=Course.Status.ACTIVE, is_active=True).order_by('academic_year', 'name', 'pk').values('id', 'name', 'academic_year')
            search = Q(name__icontains=values['q']) | Q(academic_year__icontains=values['q'])
        else:
            queryset = User.objects.filter(role=User.Role.TEACHER, is_active=True, deleted_at__isnull=True).order_by(
                'display_name', 'username', 'pk').values('id', 'display_name', 'username')
            search = Q(display_name__icontains=values['q']) | Q(username__icontains=values['q'])

        if values.get('exclude_id'):
            queryset = queryset.exclude(pk=values['exclude_id'])
        selected = queryset.filter(pk=values['selected_id']).first() if values.get('selected_id') else None
        if values['q']:
            queryset = queryset.filter(search)
        total = queryset.count()
        offset = (values['page'] - 1) * 10
        result = {'items': [_option(kind, row) for row in queryset[offset:offset + 10]],
                  'total': total, 'page': values['page'], 'total_pages': (total + 9) // 10}
        if selected is not None:
            result['selected'] = _option(kind, selected)
        return Response(result)
