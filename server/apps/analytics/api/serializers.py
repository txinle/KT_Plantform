from rest_framework import serializers


DATA_STATE_CHOICES = ("available", "no_data", "partial", "conflict")
STUDENT_STATUS_CHOICES = ("completed", "in_progress", "not_participated")


class CourseReferenceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    code = serializers.CharField()
    term = serializers.CharField()
    status = serializers.CharField()


class TeacherClassPageMetadataSerializer(serializers.Serializer):
    number = serializers.IntegerField(min_value=1)
    size = serializers.IntegerField(min_value=1)
    total_items = serializers.IntegerField(min_value=0)
    total_pages = serializers.IntegerField(min_value=1)
    start = serializers.IntegerField(min_value=0)
    end = serializers.IntegerField(min_value=0)


class TeacherClassSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    academic_year = serializers.CharField()
    is_active = serializers.BooleanField()
    active_student_count = serializers.IntegerField(min_value=0)
    detail_url = serializers.CharField()


class TeacherClassCapabilitiesSerializer(serializers.Serializer):
    can_create = serializers.BooleanField()
    can_manage = serializers.BooleanField()


class TeacherClassCollectionSerializer(serializers.Serializer):
    items = TeacherClassSummarySerializer(many=True)
    page = TeacherClassPageMetadataSerializer()
    course = CourseReferenceSerializer()
    capabilities = TeacherClassCapabilitiesSerializer()


class TeacherClassCreateRequestSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)
    academic_year = serializers.CharField(
        max_length=32,
        allow_blank=True,
        required=False,
    )


class TeacherClassCreateResponseSerializer(serializers.Serializer):
    class_group = TeacherClassSummarySerializer()


class MetricValueSerializer(serializers.Serializer):
    numerator = serializers.DecimalField(max_digits=20, decimal_places=8)
    denominator = serializers.DecimalField(max_digits=20, decimal_places=8)
    ratio = serializers.DecimalField(
        max_digits=20,
        decimal_places=8,
        allow_null=True,
    )
    state = serializers.ChoiceField(choices=DATA_STATE_CHOICES)
    sample_size = serializers.IntegerField(allow_null=True, min_value=0)
    percent_display = serializers.CharField()


class ClassGroupReferenceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    academic_year = serializers.CharField()
    is_active = serializers.BooleanField()
    active_student_count = serializers.IntegerField(min_value=0)


class ClassAnalysisScopeSerializer(serializers.Serializer):
    course_id = serializers.IntegerField()
    class_group_id = serializers.IntegerField()
    experiment_id = serializers.IntegerField(allow_null=True)
    task_count = serializers.IntegerField(min_value=0)


class ClassOverviewSerializer(serializers.Serializer):
    roster_count = serializers.IntegerField(min_value=0)
    participated_count = serializers.IntegerField(min_value=0)
    correct_rate = MetricValueSerializer()
    completion_rate = MetricValueSerializer()


class DistributionBinSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    count = serializers.IntegerField(min_value=0)


class ParticipationBreakdownSerializer(serializers.Serializer):
    completed = serializers.IntegerField(min_value=0)
    in_progress = serializers.IntegerField(min_value=0)
    not_participated = serializers.IntegerField(min_value=0)


class KCRateRowSerializer(serializers.Serializer):
    name = serializers.CharField()
    correct_rate = MetricValueSerializer()


class ExperimentOptionSerializer(serializers.Serializer):
    experiment_id = serializers.IntegerField()
    name = serializers.CharField()
    published_at = serializers.DateTimeField()


class ExperimentSummaryRowSerializer(ExperimentOptionSerializer):
    is_live = serializers.BooleanField()
    eligible_count = serializers.IntegerField(min_value=0)
    participated_count = serializers.IntegerField(min_value=0)
    completed_count = serializers.IntegerField(min_value=0)
    correct_rate = MetricValueSerializer()
    score_rate = MetricValueSerializer()
    analysis_url = serializers.CharField()


class ExperimentParticipantRowSerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    student_number = serializers.CharField()
    display_name = serializers.CharField()
    is_current_member = serializers.BooleanField()
    participated = serializers.BooleanField()
    completed = serializers.BooleanField()
    correct_rate = MetricValueSerializer()
    score_rate = MetricValueSerializer()
    rank = serializers.IntegerField(allow_null=True, min_value=1)
    rank_sample_size = serializers.IntegerField(min_value=0)
    student_url = serializers.CharField()


class StudentSummaryRowSerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    student_number = serializers.CharField()
    display_name = serializers.CharField()
    completed_experiments = serializers.IntegerField(min_value=0)
    eligible_experiments = serializers.IntegerField(min_value=0)
    correct_rate = MetricValueSerializer()
    score_rate = MetricValueSerializer()
    average_answer_time_ms = serializers.DecimalField(
        max_digits=20,
        decimal_places=3,
        allow_null=True,
    )
    average_answer_time_display = serializers.CharField()
    status = serializers.ChoiceField(choices=STUDENT_STATUS_CHOICES)
    rank = serializers.IntegerField(allow_null=True, min_value=1)
    student_url = serializers.CharField()


class StudentCollectionSerializer(serializers.Serializer):
    items = StudentSummaryRowSerializer(many=True)
    page = TeacherClassPageMetadataSerializer()


class QuestionSummaryRowSerializer(serializers.Serializer):
    question_snapshot_id = serializers.IntegerField()
    code = serializers.CharField()
    text = serializers.CharField()
    kcs = serializers.ListField(child=serializers.CharField())
    answer_count = serializers.IntegerField(min_value=0)
    correct_count = serializers.IntegerField(min_value=0)
    incorrect_count = serializers.IntegerField(min_value=0)
    correct_rate = MetricValueSerializer()
    average_answer_time_ms = serializers.DecimalField(
        max_digits=20,
        decimal_places=3,
        allow_null=True,
    )
    average_answer_time_display = serializers.CharField()
    detail_url = serializers.CharField()


class ClassCenterFiltersSerializer(serializers.Serializer):
    query = serializers.CharField()
    sort = serializers.CharField()
    status = serializers.CharField(allow_null=True)
    experiment_id = serializers.IntegerField(allow_null=True)


class ClassCenterActionsSerializer(serializers.Serializer):
    class_list_url = serializers.CharField()
    visibility_url = serializers.CharField()
    manage_url = serializers.CharField()
    import_students_url = serializers.CharField()


class TeacherClassCenterSerializer(serializers.Serializer):
    course = CourseReferenceSerializer()
    class_group = ClassGroupReferenceSerializer()
    generated_at = serializers.DateTimeField()
    scope = ClassAnalysisScopeSerializer()
    has_live_experiments = serializers.BooleanField()
    rank_sample_size = serializers.IntegerField(min_value=0)
    overview = ClassOverviewSerializer()
    class_average_correct_rate = MetricValueSerializer()
    correct_rate_distribution = DistributionBinSerializer(many=True)
    participation = ParticipationBreakdownSerializer()
    kc_rows = KCRateRowSerializer(many=True)
    experiment_options = ExperimentOptionSerializer(many=True)
    experiment_rows = ExperimentSummaryRowSerializer(many=True)
    experiment_participants = ExperimentParticipantRowSerializer(many=True)
    students = StudentCollectionSerializer()
    question_rows = QuestionSummaryRowSerializer(many=True)
    filters = ClassCenterFiltersSerializer()
    actions = ClassCenterActionsSerializer()


class DataCenterExperimentVersionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    version_number = serializers.IntegerField(min_value=1)


class DataCenterExperimentOptionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    versions = DataCenterExperimentVersionSerializer(many=True)


class DataCenterClassOptionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    academic_year = serializers.CharField()


class DataCenterTeachingScopeSerializer(serializers.Serializer):
    experiment_id = serializers.IntegerField(allow_null=True)
    experiment_ids = serializers.ListField(child=serializers.IntegerField(), required=False)
    class_id = serializers.IntegerField(allow_null=True)
    is_complete = serializers.BooleanField()
    display = serializers.CharField()


class DataCenterScopeSerializer(serializers.Serializer):
    course = CourseReferenceSerializer()
    current = DataCenterTeachingScopeSerializer()
    experiments = DataCenterExperimentOptionSerializer(many=True)
    classes = DataCenterClassOptionSerializer(many=True)
    scope_update_url = serializers.CharField()


class DataCenterScopeUpdateSerializer(serializers.Serializer):
    experiment_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    experiment_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False)
    class_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)


class DataCenterExportJobCreateSerializer(serializers.Serializer):
    package = serializers.CharField()
    confirm_identified = serializers.BooleanField()
    scope = serializers.JSONField()
    tables = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )
    fields = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )


class DataCenterExportJobMutationSerializer(serializers.Serializer):
    retry_after_seconds = serializers.IntegerField(min_value=5, max_value=30, required=False)
    reused = serializers.BooleanField(required=False)
    source_version = serializers.CharField(required=False)
    job_id = serializers.UUIDField()
    package = serializers.CharField()
    status = serializers.CharField()
    status_label = serializers.CharField()
    download_url = serializers.CharField(allow_null=True)
    history_url = serializers.CharField()


class DataCenterFactsSerializer(serializers.Serializer):
    participants = serializers.IntegerField(min_value=0)
    observed_participants = serializers.IntegerField(min_value=0)
    completed_sessions = serializers.IntegerField(min_value=0)
    total_sessions = serializers.IntegerField(min_value=0)
    submitted_attempts = serializers.IntegerField(min_value=0)
    quality_warnings = serializers.IntegerField(min_value=0)
    process_records = serializers.IntegerField(min_value=0)
    not_evaluable = serializers.IntegerField(min_value=0)


class DataCenterRecentExportSerializer(serializers.Serializer):
    package_label = serializers.CharField()
    class_name = serializers.CharField()
    job_id = serializers.UUIDField()
    package = serializers.CharField()
    experiment_name = serializers.CharField(allow_null=True)
    status = serializers.CharField()
    status_label = serializers.CharField()
    created_at = serializers.DateTimeField()


class DataCenterOverviewActionsSerializer(serializers.Serializer):
    preview_url = serializers.CharField()
    scope_update_url = serializers.CharField()
    governance_url = serializers.CharField()
    teaching_url = serializers.CharField()
    exports_url = serializers.CharField()
    quality_url = serializers.CharField()
    history_url = serializers.CharField()


class DataCenterOverviewSerializer(serializers.Serializer):
    course = CourseReferenceSerializer()
    scope = DataCenterTeachingScopeSerializer()
    facts = DataCenterFactsSerializer()
    recent_exports = DataCenterRecentExportSerializer(many=True)
    actions = DataCenterOverviewActionsSerializer()


class DataCenterExportPackageSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    description = serializers.CharField()
    blocked = serializers.BooleanField()


class DataCenterResearchScopeSerializer(serializers.Serializer):
    values = serializers.JSONField()
    query = serializers.CharField()
    display = serializers.CharField()
    is_single_version = serializers.BooleanField()
    is_complete = serializers.BooleanField()


class DataCenterExportActionsSerializer(serializers.Serializer):
    create_url = serializers.CharField()
    reset_url = serializers.CharField()


class DataCenterAdvancedTableSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    path = serializers.CharField()
    description = serializers.CharField()
    total_items = serializers.IntegerField(min_value=0)
    level = serializers.CharField(required=False)
    format = serializers.CharField(required=False)


class DataCenterExportExperimentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class DataCenterExportPreparationSerializer(serializers.Serializer):
    state = serializers.CharField()
    ready_count = serializers.IntegerField(min_value=0)
    pending_count = serializers.IntegerField(min_value=0)


class DataCenterSessionOptionSerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()


class DataCenterExportCatalogSerializer(serializers.Serializer):
    course = CourseReferenceSerializer()
    scope = DataCenterResearchScopeSerializer()
    packages = DataCenterExportPackageSerializer(many=True)
    selected_package = DataCenterExportPackageSerializer(allow_null=True)
    export_versions = DataCenterExperimentVersionSerializer(many=True)
    selected_export_version = DataCenterExperimentVersionSerializer(allow_null=True)
    identified_data_notice = serializers.CharField()
    actions = DataCenterExportActionsSerializer()
    tables = DataCenterAdvancedTableSerializer(many=True)
    experiments = DataCenterExportExperimentSerializer(many=True)
    class_choices = DataCenterClassOptionSerializer(many=True)
    preparation = DataCenterExportPreparationSerializer()
    generated_at = serializers.DateTimeField()


class DataCenterQualityRowSerializer(serializers.Serializer):
    student_number = serializers.CharField()
    student_name = serializers.CharField()
    session_ref = serializers.CharField()
    question_code = serializers.CharField()
    state = serializers.CharField()
    state_label = serializers.CharField()
    problem = serializers.CharField()
    impact = serializers.CharField()


class DataCenterPreparationSerializer(serializers.Serializer):
    retry_after_seconds = serializers.IntegerField(min_value=5, max_value=30, required=False)
    state = serializers.CharField()
    message = serializers.CharField()
    updated_at = serializers.DateTimeField(allow_null=True)
    result_version = serializers.CharField(allow_null=True)
    projection_version = serializers.CharField()
    stale = serializers.BooleanField()
    ready_count = serializers.IntegerField(min_value=0)
    pending_count = serializers.IntegerField(min_value=0)
    failed_count = serializers.IntegerField(min_value=0)
    updating_count = serializers.IntegerField(min_value=0)
    oldest_pending_seconds = serializers.IntegerField(min_value=0, allow_null=True)


class DataCenterQualityCollectionSerializer(serializers.Serializer):
    items = DataCenterQualityRowSerializer(many=True)
    page = TeacherClassPageMetadataSerializer()
    course = CourseReferenceSerializer()
    scope = DataCenterResearchScopeSerializer()
    counts = serializers.DictField(child=serializers.IntegerField(min_value=0))
    preparation = DataCenterPreparationSerializer(required=False)


class DataCenterAdvancedStatusSerializer(serializers.Serializer):
    preparation = DataCenterPreparationSerializer()


class DataCenterExportHistoryRowSerializer(serializers.Serializer):
    package_label = serializers.CharField()
    class_name = serializers.CharField()
    artifact_removal_label = serializers.CharField()
    job_id = serializers.UUIDField()
    package = serializers.CharField()
    experiment_name = serializers.CharField(allow_null=True)
    status = serializers.CharField()
    status_label = serializers.CharField()
    artifact_removal_reason = serializers.CharField()
    created_by_name = serializers.CharField()
    created_at = serializers.DateTimeField()
    download_url = serializers.CharField(allow_null=True)
    delete_url = serializers.CharField(allow_null=True)


class DataCenterExportHistoryCollectionSerializer(serializers.Serializer):
    scope = DataCenterResearchScopeSerializer()
    items = DataCenterExportHistoryRowSerializer(many=True)
    page = TeacherClassPageMetadataSerializer()
    course = CourseReferenceSerializer()


class DataCenterPreviewColumnSerializer(serializers.Serializer):
    name = serializers.CharField()
    description = serializers.CharField()


class DataCenterAdvancedPreviewSerializer(serializers.Serializer):
    columns = DataCenterPreviewColumnSerializer(many=True)
    rows = serializers.ListField(
        child=serializers.ListField(child=serializers.JSONField())
    )
    page = TeacherClassPageMetadataSerializer()
    cursor = serializers.DictField(required=False)
    data_status = serializers.CharField()


class DataCenterStudentSearchItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    student_number = serializers.CharField()
    name = serializers.CharField()


class DataCenterStudentSearchSerializer(serializers.Serializer):
    items = DataCenterStudentSearchItemSerializer(many=True)
    limit = serializers.IntegerField()


class DataCenterAdvancedSerializer(serializers.Serializer):
    view = serializers.CharField()
    workspace = serializers.JSONField()
    course = CourseReferenceSerializer()
    scope = DataCenterResearchScopeSerializer()
    search = serializers.CharField()
    tables = DataCenterAdvancedTableSerializer(many=True)
    session_options = DataCenterSessionOptionSerializer(many=True)
    generated_at = serializers.DateTimeField()
    selected_table = DataCenterAdvancedTableSerializer()
    selected_experiment = DataCenterExperimentOptionSerializer(allow_null=True)
    preview = DataCenterAdvancedPreviewSerializer(allow_null=True)
    preparation = DataCenterPreparationSerializer(required=False)
