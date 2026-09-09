from rest_framework import serializers

from apps.accounts.models import Course


class CourseContextSerializer(serializers.ModelSerializer):
    can_manage = serializers.SerializerMethodField()

    def get_can_manage(self, course) -> bool:
        request = self.context.get("request")
        return bool(request and course.can_manage(request.user) and course.status == course.Status.ACTIVE)

    class Meta:
        model = Course
        fields = ("id", "name", "code", "term", "status", "can_manage")
        read_only_fields = fields


class TeacherCourseSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    code = serializers.CharField()
    term = serializers.CharField()
    description = serializers.CharField()
    owner_name = serializers.CharField()
    status = serializers.ChoiceField(choices=Course.Status.choices)
    status_label = serializers.CharField()
    class_count = serializers.IntegerField(min_value=0)
    bank_count = serializers.IntegerField(min_value=0)
    experiment_count = serializers.IntegerField(min_value=0)
    detail_url = serializers.CharField()


class TeacherCourseCollectionSerializer(serializers.Serializer):
    items = TeacherCourseSummarySerializer(many=True)
    create_url = serializers.CharField()


class TeacherCourseCreateRequestSerializer(serializers.Serializer):
    name = serializers.CharField()
    code = serializers.CharField()
    term = serializers.CharField()
    description = serializers.CharField(allow_blank=True, required=False)


class TeacherCourseCreateResponseSerializer(serializers.Serializer):
    course = TeacherCourseSummarySerializer()


class TeacherCourseDetailCourseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    code = serializers.CharField()
    term = serializers.CharField()
    description = serializers.CharField()
    owner_name = serializers.CharField()
    status = serializers.ChoiceField(choices=Course.Status.choices)
    status_label = serializers.CharField()
    updated_at = serializers.DateTimeField()
    detail_url = serializers.CharField()
    banks_url = serializers.CharField()
    classes_url = serializers.CharField()
    experiments_url = serializers.CharField()
    data_url = serializers.CharField()
    settings_url = serializers.CharField()


class TeacherCourseMetricsSerializer(serializers.Serializer):
    class_count = serializers.IntegerField(min_value=0)
    bank_count = serializers.IntegerField(min_value=0)
    experiment_count = serializers.IntegerField(min_value=0)
    student_count = serializers.IntegerField(min_value=0)


class TeacherCourseRecentExperimentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    question_bank_name = serializers.CharField()
    status = serializers.CharField()
    status_label = serializers.CharField()
    detail_url = serializers.CharField()


class TeacherCourseRecentBankSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    description = serializers.CharField()
    question_count = serializers.IntegerField(min_value=0)
    visibility = serializers.CharField()
    visibility_label = serializers.CharField()
    updated_at = serializers.DateTimeField()
    detail_url = serializers.CharField()


class TeacherCourseDetailSerializer(serializers.Serializer):
    course = TeacherCourseDetailCourseSerializer()
    metrics = TeacherCourseMetricsSerializer()
    recent_experiments = TeacherCourseRecentExperimentSerializer(many=True)
    recent_banks = TeacherCourseRecentBankSerializer(many=True)
    class_center_enabled = serializers.BooleanField()
    is_course_owner = serializers.BooleanField()


class TeacherCourseSettingsCourseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    code = serializers.CharField()
    term = serializers.CharField()
    description = serializers.CharField()
    status = serializers.ChoiceField(choices=Course.Status.choices)
    status_label = serializers.CharField()


class TeacherCourseTeacherOptionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    display_name = serializers.CharField()
    username = serializers.CharField()


class TeacherCourseSettingsFormSerializer(serializers.Serializer):
    name = serializers.CharField()
    code = serializers.CharField()
    term = serializers.CharField()
    description = serializers.CharField()
    state_token = serializers.CharField()
    teachers = serializers.ListField(child=serializers.IntegerField())
    teacher_options = TeacherCourseTeacherOptionSerializer(many=True)


class TeacherCoursePurgeRequestSerializer(serializers.Serializer):
    purge_request_id = serializers.UUIDField()
    preview_session_count = serializers.IntegerField(min_value=0)
    preview_event_count = serializers.IntegerField(min_value=0)
    status = serializers.CharField()
    status_label = serializers.CharField()
    execute_url = serializers.CharField(allow_null=True)


class TeacherCourseSettingsCapabilitiesSerializer(serializers.Serializer):
    can_update = serializers.BooleanField()
    can_archive = serializers.BooleanField()


class TeacherCourseSettingsSerializer(serializers.Serializer):
    course = TeacherCourseSettingsCourseSerializer()
    form = TeacherCourseSettingsFormSerializer()
    capabilities = TeacherCourseSettingsCapabilitiesSerializer()
    archive_url = serializers.CharField()
    purge_request_create_url = serializers.CharField()
    purge_requests = TeacherCoursePurgeRequestSerializer(many=True)


class TeacherCourseSettingsRequestSerializer(TeacherCourseCreateRequestSerializer):
    state_token = serializers.CharField()
    teachers = serializers.ListField(child=serializers.IntegerField(), required=False)


class TeacherCourseArchiveRequestSerializer(serializers.Serializer):
    confirmation = serializers.ChoiceField(choices=("archive",))


class TeacherCoursePurgeCreateRequestSerializer(serializers.Serializer):
    confirmation = serializers.ChoiceField(choices=("PURGE",))
    reason_code = serializers.ChoiceField(
        choices=("research_withdrawal", "collection_error", "retention_expired")
    )


class TeacherCoursePurgeExecuteRequestSerializer(serializers.Serializer):
    confirmation = serializers.ChoiceField(choices=("execute",))
