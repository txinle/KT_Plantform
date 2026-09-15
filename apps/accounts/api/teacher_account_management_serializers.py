from rest_framework import serializers

from apps.accounts.models import ClassStudentAnalyticsPolicy, Course


class TeacherAccountClassSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    academic_year = serializers.CharField()
    is_active = serializers.BooleanField()
    course_id = serializers.IntegerField(allow_null=True)
    student_count = serializers.IntegerField(min_value=0)


class TeacherAccountClassOptionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    academic_year = serializers.CharField()
    display_name = serializers.CharField()
    is_active = serializers.BooleanField()


class TeacherAccountClassCollectionSerializer(serializers.Serializer):
    items = TeacherAccountClassSerializer(many=True)


class TeacherAccountClassCreateRequestSerializer(serializers.Serializer):
    name = serializers.CharField()
    academic_year = serializers.CharField(allow_blank=True, required=False)


class TeacherAccountClassCreateResponseSerializer(serializers.Serializer):
    class_group = TeacherAccountClassSerializer()


class TeacherAccountStudentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    student_number = serializers.CharField()
    display_name = serializers.CharField()
    username = serializers.CharField()
    class_group = TeacherAccountClassOptionSerializer(allow_null=True)


class TeacherAccountStudentCollectionSerializer(serializers.Serializer):
    items = TeacherAccountStudentSerializer(many=True)
    class_options = TeacherAccountClassOptionSerializer(many=True)
    query = serializers.CharField()
    selected_class_id = serializers.IntegerField(allow_null=True)


class TeacherAccountStudentCreateRequestSerializer(serializers.Serializer):
    student_number = serializers.CharField()
    display_name = serializers.CharField()
    class_group = serializers.IntegerField()


class TeacherAccountStudentCreateResponseSerializer(serializers.Serializer):
    student = TeacherAccountStudentSerializer()


class PasswordResetRequestSerializer(serializers.Serializer):
    confirmation = serializers.ChoiceField(choices=("reset",))


class StudentImportPreviewRequestSerializer(serializers.Serializer):
    file = serializers.FileField()


class StudentImportPreviewRowSerializer(serializers.Serializer):
    student_number = serializers.CharField()
    display_name = serializers.CharField()
    class_id = serializers.IntegerField()
    class_name = serializers.CharField()
    account_action = serializers.ChoiceField(choices=("create", "reuse"))


class LegacyStudentImportPreviewSerializer(serializers.Serializer):
    preview = StudentImportPreviewRowSerializer(many=True)


class CourseStudentImportPreviewSerializer(LegacyStudentImportPreviewSerializer):
    preview_id = serializers.UUIDField()
    class_state_token = serializers.CharField()


class TeacherAccountStudentImportConfirmRequestSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=("confirm",), required=False)


class LegacyStudentImportResultSerializer(serializers.Serializer):
    imported = serializers.IntegerField(min_value=0)


class TeacherAccountCourseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    code = serializers.CharField()
    term = serializers.CharField()
    status = serializers.ChoiceField(choices=Course.Status.choices)


class ManagedClassSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    academic_year = serializers.CharField()
    is_active = serializers.BooleanField()
    state_token = serializers.CharField()
    detail_url = serializers.CharField()


class ManagedEnrollmentSerializer(serializers.Serializer):
    enrollment_id = serializers.IntegerField()
    student = TeacherAccountStudentSerializer()
    identity_state_token = serializers.CharField()
    affects_other_courses = serializers.BooleanField()


class ManagedClassSourceOptionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    course_id = serializers.IntegerField()
    course_name = serializers.CharField()
    course_code = serializers.CharField()
    course_term = serializers.CharField()
    name = serializers.CharField()
    academic_year = serializers.CharField()
    display_name = serializers.CharField()


class ManagedClassCapabilitiesSerializer(serializers.Serializer):
    can_update = serializers.BooleanField()
    can_delete = serializers.BooleanField()
    can_add_students = serializers.BooleanField()
    can_move_students = serializers.BooleanField()
    can_remove_students = serializers.BooleanField()
    can_reset_passwords = serializers.BooleanField()
    can_edit_students = serializers.BooleanField()
    can_update_visibility = serializers.BooleanField()


class ManagedClassActionsSerializer(serializers.Serializer):
    detail_url = serializers.CharField()
    classes_url = serializers.CharField()
    add_students_url = serializers.CharField()
    visibility_url = serializers.CharField()
    import_template_url = serializers.CharField()


class CourseClassManagementSerializer(serializers.Serializer):
    course = TeacherAccountCourseSerializer()
    class_group = ManagedClassSerializer()
    enrollments = ManagedEnrollmentSerializer(many=True)
    target_classes = TeacherAccountClassOptionSerializer(many=True)
    source_classes = ManagedClassSourceOptionSerializer(many=True)
    capabilities = ManagedClassCapabilitiesSerializer()
    actions = ManagedClassActionsSerializer()


class CourseClassUpdateRequestSerializer(serializers.Serializer):
    name = serializers.CharField()
    academic_year = serializers.CharField(allow_blank=True)
    is_active = serializers.BooleanField()
    state_token = serializers.CharField()


class CourseClassDeleteRequestSerializer(serializers.Serializer):
    confirmation = serializers.ChoiceField(choices=("delete",))


class CourseClassDeleteResponseSerializer(serializers.Serializer):
    deleted = serializers.BooleanField()


class ManualEnrollmentRequestSerializer(serializers.Serializer):
    student_number = serializers.CharField()
    display_name = serializers.CharField()
    confirmation_token = serializers.CharField(required=False, allow_blank=False)


class StudentIdentityUpdateRequestSerializer(serializers.Serializer):
    student_number = serializers.CharField(max_length=64)
    display_name = serializers.CharField(max_length=80)
    state_token = serializers.CharField()


class StudentIdentityUpdateResponseSerializer(serializers.Serializer):
    student = TeacherAccountStudentSerializer()
    identity_state_token = serializers.CharField()
    affects_other_courses = serializers.BooleanField()


class ManualEnrollmentResponseSerializer(serializers.Serializer):
    student = TeacherAccountStudentSerializer()
    created_account = serializers.BooleanField()


class CourseClassCopyPreviewRequestSerializer(serializers.Serializer):
    source_class = serializers.IntegerField()


class CourseClassCopyPreviewRowSerializer(serializers.Serializer):
    student = TeacherAccountStudentSerializer()
    skip = serializers.BooleanField()


class CourseClassCopyPreviewSerializer(serializers.Serializer):
    source_class = ManagedClassSourceOptionSerializer()
    preview = CourseClassCopyPreviewRowSerializer(many=True)


class CourseClassCopyConfirmRequestSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=("confirm",), required=False)


class CourseClassCopyResultSerializer(serializers.Serializer):
    copied = serializers.IntegerField(min_value=0)
    skipped = serializers.IntegerField(min_value=0)


class CourseStudentImportConfirmRequestSerializer(serializers.Serializer):
    preview_id = serializers.UUIDField()
    class_state_token = serializers.CharField()


class CourseStudentImportResultSerializer(serializers.Serializer):
    job_id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=("queued", "running", "succeeded", "failed"))
    total_rows = serializers.IntegerField(min_value=0)
    processed_rows = serializers.IntegerField(min_value=0)
    created_accounts = serializers.IntegerField(min_value=0)
    enrollments = serializers.IntegerField(min_value=0)
    progress_stage = serializers.CharField()
    error_code = serializers.CharField(allow_blank=True)
    error_message = serializers.CharField(allow_blank=True)


class MoveEnrollmentRequestSerializer(serializers.Serializer):
    class_group = serializers.IntegerField()


class MoveEnrollmentResponseSerializer(serializers.Serializer):
    class_group = TeacherAccountClassOptionSerializer()


class RemoveEnrollmentRequestSerializer(serializers.Serializer):
    confirmation = serializers.ChoiceField(choices=("delete",))


class RemoveEnrollmentResponseSerializer(serializers.Serializer):
    removed = serializers.BooleanField()


class StudentVisibilityPolicySerializer(serializers.Serializer):
    show_scores = serializers.BooleanField()
    show_correct_rate = serializers.BooleanField()
    show_item_results = serializers.BooleanField()
    show_class_average = serializers.BooleanField()
    rank_mode = serializers.ChoiceField(
        choices=ClassStudentAnalyticsPolicy.RankMode.choices
    )
    show_kc_rates = serializers.BooleanField()
    show_trend = serializers.BooleanField()
    is_read_only = serializers.BooleanField()


class StudentVisibilityPolicyRequestSerializer(serializers.Serializer):
    show_scores = serializers.BooleanField()
    show_correct_rate = serializers.BooleanField()
    show_item_results = serializers.BooleanField()
    show_class_average = serializers.BooleanField()
    rank_mode = serializers.ChoiceField(
        choices=ClassStudentAnalyticsPolicy.RankMode.choices
    )
    show_kc_rates = serializers.BooleanField()
    show_trend = serializers.BooleanField()
