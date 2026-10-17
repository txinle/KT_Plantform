from rest_framework import serializers
from drf_spectacular.utils import PolymorphicProxySerializer, extend_schema_serializer


class StudentCourseSerializer(serializers.Serializer):
    course_id = serializers.IntegerField()
    course_name = serializers.CharField()
    course_code = serializers.CharField()
    course_term = serializers.CharField()
    course_status = serializers.CharField()
    class_name = serializers.CharField()


class StudentCourseCollectionSerializer(serializers.Serializer):
    items = StudentCourseSerializer(many=True)


class StudentTaskProgressSerializer(serializers.Serializer):
    completed = serializers.IntegerField()
    total = serializers.IntegerField()


class StudentTaskSerializer(serializers.Serializer):
    task_id = serializers.IntegerField()
    experiment_id = serializers.IntegerField()
    experiment_name = serializers.CharField()
    class_name = serializers.CharField()
    business_status = serializers.CharField()
    opens_at = serializers.DateTimeField(allow_null=True)
    closes_at = serializers.DateTimeField(allow_null=True)
    progress = StudentTaskProgressSerializer()
    primary_action = serializers.CharField(allow_null=True)
    available_actions = serializers.ListField(child=serializers.CharField())
    feedback_mode = serializers.CharField()


class StudentTaskCollectionSerializer(serializers.Serializer):
    course = StudentCourseSerializer()
    items = StudentTaskSerializer(many=True)


class StudentSessionEntrySerializer(serializers.Serializer):
    task_id = serializers.IntegerField()
    session_id = serializers.IntegerField()
    question_url = serializers.CharField()
    admission_token = serializers.CharField(required=False, read_only=True)
    admission_action = serializers.CharField(required=False, read_only=True)
    runtime = serializers.JSONField(required=False, read_only=True)
    admission_execution_id = serializers.UUIDField(required=False, allow_null=True, read_only=True)


class StudentPreparedStartSerializer(serializers.Serializer):
    admission_token = serializers.CharField(required=False, max_length=128)
    client_instance_id = serializers.UUIDField(required=False)
    navigation_id = serializers.UUIDField(required=False)
    client_occurred_at = serializers.DateTimeField(required=False)
    client_monotonic_ms = serializers.DecimalField(max_digits=20, decimal_places=3, required=False)
    client_time_origin = serializers.DateTimeField(required=False)


class StudentPreparedStartPendingSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=('executing',))
    retry_after_seconds = serializers.IntegerField(min_value=1)


class StudentAdmissionStatusSerializer(serializers.Serializer):
    admission_token = serializers.CharField()
    action = serializers.ChoiceField(choices=('start', 'resume'))


class StudentAdmissionStatusResponseSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=('queued', 'admitted', 'executing', 'succeeded', 'failed'))
    admission_token = serializers.CharField()
    poll_url = serializers.CharField()
    retry_after_seconds = serializers.IntegerField(min_value=1)


class StudentRuntimeAssignmentSerializer(serializers.Serializer):
    assignment_id = serializers.IntegerField()
    attempt_id = serializers.IntegerField()
    status = serializers.CharField()
    sequence_index = serializers.IntegerField()


class StudentRuntimeProgressSerializer(serializers.Serializer):
    submitted = serializers.IntegerField()
    total = serializers.IntegerField()


class StudentRuntimeOptionSerializer(serializers.Serializer):
    key = serializers.CharField()
    text = serializers.CharField()


class StudentRuntimeQuestionSerializer(serializers.Serializer):
    snapshot_id = serializers.IntegerField()
    stable_code = serializers.CharField(allow_blank=True)
    code = serializers.CharField()
    question_type = serializers.CharField()
    text = serializers.CharField()
    primary_kc = serializers.CharField()
    options = StudentRuntimeOptionSerializer(many=True)


class StudentRuntimeNavigationItemSerializer(serializers.Serializer):
    assignment_id = serializers.IntegerField()
    sequence_index = serializers.IntegerField()
    status = serializers.CharField()
    url = serializers.CharField()
    current = serializers.BooleanField()


class StudentRuntimeActionsSerializer(serializers.Serializer):
    research_tools_url = serializers.CharField(required=False)
    resolve_branch_url = serializers.CharField(required=False)
    save_draft_url = serializers.CharField()
    finish_url = serializers.CharField()


class StudentRuntimeQuestionEntryQuerySerializer(serializers.Serializer):
    reason = serializers.CharField(default="navigation_click", max_length=32)
    navigation_id = serializers.UUIDField(required=False)
    client_occurred_at = serializers.DateTimeField(required=False)
    client_monotonic_ms = serializers.DecimalField(
        required=False,
        max_digits=20,
        decimal_places=3,
        min_value=0,
    )
    client_time_origin = serializers.DateTimeField(required=False)

    def validate(self, attrs):
        navigation_fields = {
            "navigation_id",
            "client_occurred_at",
            "client_monotonic_ms",
            "client_time_origin",
        }
        supplied = navigation_fields.intersection(attrs)
        if supplied and supplied != navigation_fields:
            raise serializers.ValidationError(
                "导航标识、客户端墙钟、单调时钟和时钟原点必须一起提供。"
            )
        return attrs


class StudentRuntimeQuestionEnterSerializer(StudentRuntimeQuestionEntryQuerySerializer):
    client_instance_id = serializers.UUIDField(required=False)
    reason = serializers.ChoiceField(choices=('navigation_click', 'resume'), default='navigation_click')
    navigation_id = serializers.UUIDField()
    client_occurred_at = serializers.DateTimeField()
    client_monotonic_ms = serializers.DecimalField(max_digits=20, decimal_places=3, min_value=0)
    client_time_origin = serializers.DateTimeField()
    admission_token = serializers.CharField(required=False, write_only=True, max_length=128)
    admission_action = serializers.ChoiceField(choices=('start', 'resume'), required=False, write_only=True)

    def validate(self, attrs):
        if set(self.initial_data) - set(self.fields):
            raise serializers.ValidationError('进入请求包含未知字段。')
        return super().validate(attrs)


class StudentRuntimeEntryReceiptSerializer(serializers.Serializer):
    navigation_id = serializers.UUIDField()
    session_id = serializers.IntegerField()
    assignment_id = serializers.IntegerField()
    attempt_id = serializers.IntegerField()
    visit_id = serializers.IntegerField()
    visit_index = serializers.IntegerField()
    entry_event_id = serializers.UUIDField()
    entered_at = serializers.DateTimeField()


class AtomicSubmissionCapabilitySerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    mutation_receipts = serializers.BooleanField(required=False)
    version = serializers.IntegerField()
    max_events = serializers.IntegerField()
    max_checkpoints = serializers.IntegerField()
    max_bytes = serializers.IntegerField()


class TransitionEntryCapabilitySerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    version = serializers.IntegerField()


class StudentRuntimeProcessSerializer(serializers.Serializer):
    paper_flow_version = serializers.IntegerField(required=False)
    transition_entry = TransitionEntryCapabilitySerializer(required=False)
    capture_bundle = AtomicSubmissionCapabilitySerializer(required=False)
    close_bundle = AtomicSubmissionCapabilitySerializer(required=False)
    atomic_submission = AtomicSubmissionCapabilitySerializer(required=False)
    save_and_leave = AtomicSubmissionCapabilitySerializer(required=False)
    writer_lease_acquire_url = serializers.CharField(required=False)
    writer_lease_renew_url = serializers.CharField(required=False)
    writer_lease_release_url = serializers.CharField(required=False)
    capture_version = serializers.CharField(required=False)
    active_response_event_id = serializers.CharField(allow_null=True, required=False)
    response_version = serializers.IntegerField(required=False)
    visit_id = serializers.IntegerField(allow_null=True)
    visit_index = serializers.IntegerField(allow_null=True)
    entry_event_id = serializers.UUIDField(allow_null=True)
    navigation_id = serializers.UUIDField(allow_null=True)
    session_id = serializers.IntegerField()
    attempt_id = serializers.IntegerField()
    event_batch_url = serializers.CharField()
    checkpoint_url = serializers.CharField()
    time_sync_create_url = serializers.CharField()
    time_sync_finalize_url = serializers.CharField()
    app_version = serializers.CharField()
    heartbeat_interval_seconds = serializers.IntegerField()


class StudentRuntimeOfflineAuthorizationSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    session_id = serializers.IntegerField()
    assignment_id = serializers.IntegerField()


class StudentRuntimeQuestionEntrySerializer(serializers.Serializer):
    assessment_flow_version = serializers.IntegerField(required=False)
    prepared_paper = serializers.JSONField(required=False)
    initial_writer_lease = serializers.JSONField(required=False)
    inline_confidence = serializers.JSONField(required=False)
    offline_authorization = StudentRuntimeOfflineAuthorizationSerializer()
    entry_receipt = StudentRuntimeEntryReceiptSerializer(required=False)
    entry_allowed = serializers.BooleanField()
    response_write_allowed = serializers.BooleanField(required=False)
    entry_denial_code = serializers.CharField(allow_blank=True)
    presented_deadline_at = serializers.DateTimeField(required=False, allow_null=True)
    research_tools = serializers.JSONField(required=False)
    task_id = serializers.IntegerField()
    session_id = serializers.IntegerField()
    session_status = serializers.CharField()
    experiment_name = serializers.CharField()
    assignment = StudentRuntimeAssignmentSerializer()
    progress = StudentRuntimeProgressSerializer()
    remaining_seconds = serializers.IntegerField(allow_null=True)
    question = StudentRuntimeQuestionSerializer()
    draft_answer = serializers.JSONField(allow_null=True)
    initial_response_state = serializers.JSONField(allow_null=True)
    pending_confidence = serializers.JSONField(allow_null=True)
    navigation = StudentRuntimeNavigationItemSerializer(many=True)
    actions = StudentRuntimeActionsSerializer()
    process = StudentRuntimeProcessSerializer()


class StudentResultScoreSerializer(serializers.Serializer):
    earned = serializers.DecimalField(max_digits=12, decimal_places=2)
    maximum = serializers.DecimalField(max_digits=12, decimal_places=2)
    percent = serializers.DecimalField(max_digits=6, decimal_places=1)


class StudentResultCorrectnessSummarySerializer(serializers.Serializer):
    correct = serializers.IntegerField()
    graded = serializers.IntegerField()
    percent = serializers.DecimalField(max_digits=6, decimal_places=1)


class StudentResultCorrectnessItemSerializer(serializers.Serializer):
    question_id = serializers.IntegerField()
    question_code = serializers.CharField()
    submitted_answer = serializers.CharField()
    is_correct = serializers.BooleanField(required=False, allow_null=True)
    score = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True
    )
    max_score = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True
    )


class StudentResultFullItemSerializer(StudentResultCorrectnessItemSerializer):
    correct_answer = serializers.CharField(required=False, allow_null=True)
    explanation = serializers.CharField(required=False, allow_blank=True)


class StudentComparisonBranchSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=("available", "unavailable"))
    reason = serializers.CharField(required=False)
    percent = serializers.DecimalField(max_digits=6, decimal_places=1, required=False)
    position = serializers.IntegerField(required=False)
    sample_size = serializers.IntegerField(required=False)


@extend_schema_serializer(component_name="StudentResultComparison")
class StudentComparisonSerializer(serializers.Serializer):
    class_average = StudentComparisonBranchSerializer(required=False)
    rank = StudentComparisonBranchSerializer(required=False)


class StudentResultBaseSerializer(serializers.Serializer):
    version = serializers.CharField(required=False)
    comparison_status = serializers.ChoiceField(choices=("pending", "updating", "ready", "failed"), required=False)
    comparison_generated_at = serializers.DateTimeField(required=False)
    task_id = serializers.IntegerField()
    completion_status = serializers.CharField()
    submission_status = serializers.CharField()
    completed_at = serializers.DateTimeField(allow_null=True)
    grading_status = serializers.CharField()


class StudentResultNoneSerializer(StudentResultBaseSerializer):
    feedback_mode = serializers.ChoiceField(choices=("none",))


class StudentResultCorrectnessSerializer(StudentResultBaseSerializer):
    feedback_mode = serializers.ChoiceField(choices=("correctness",))
    items = StudentResultCorrectnessItemSerializer(many=True, required=False)
    score = StudentResultScoreSerializer(required=False)
    correctness = StudentResultCorrectnessSummarySerializer(required=False)
    comparison = StudentComparisonSerializer(required=False)


class StudentResultFullSerializer(StudentResultBaseSerializer):
    feedback_mode = serializers.ChoiceField(choices=("full",))
    items = StudentResultFullItemSerializer(many=True, required=False)
    score = StudentResultScoreSerializer(required=False)
    correctness = StudentResultCorrectnessSummarySerializer(required=False)
    comparison = StudentComparisonSerializer(required=False)


StudentResultResponseSerializer = PolymorphicProxySerializer(
    component_name="StudentResult",
    serializers={
        "none": StudentResultNoneSerializer,
        "correctness": StudentResultCorrectnessSerializer,
        "full": StudentResultFullSerializer,
    },
    resource_type_field_name="feedback_mode",
)


def serialize_student_result(result):
    serializer_class = {
        "none": StudentResultNoneSerializer,
        "correctness": StudentResultCorrectnessSerializer,
        "full": StudentResultFullSerializer,
    }[result["feedback_mode"]]
    serialized = serializer_class(instance=result).data

    def retain_source_shape(value, source):
        if isinstance(value, dict) and isinstance(source, dict):
            return {
                key: retain_source_shape(value[key], source[key])
                for key in source
                if key in value
            }
        if isinstance(value, list) and isinstance(source, list):
            return [
                retain_source_shape(item, source[index])
                for index, item in enumerate(value)
                if index < len(source)
            ]
        return value

    return retain_source_shape(serialized, result)


class StudentProjectionStatusSerializer(serializers.Serializer):
    version = serializers.CharField()
    comparison_status = serializers.ChoiceField(choices=('pending', 'updating', 'ready', 'failed'), required=False)
    comparison_generated_at = serializers.DateTimeField(required=False)
    completed_at = serializers.DateTimeField(required=False, allow_null=True)


class StudentLearningProjectionSerializer(serializers.Serializer):
    version = serializers.CharField(required=False)
    comparison_status = serializers.ChoiceField(choices=("pending", "updating", "ready", "failed"), required=False)
    comparison_generated_at = serializers.DateTimeField(required=False)
    generated_at = serializers.DateTimeField()
    course_id = serializers.IntegerField()
    course_name = serializers.CharField()
    course_code = serializers.CharField()
    course_term = serializers.CharField()
    class_group_name = serializers.CharField()
    completion = serializers.DictField()
    correct_rate = serializers.DictField(required=False)
    score_rate = serializers.DictField(required=False)
    score_grading_coverage = serializers.DictField(required=False)
    comparisons = serializers.DictField(required=False)
    experiments = serializers.ListField(child=serializers.DictField())
    experiment_correct_rate_available = serializers.BooleanField(required=False)
    experiment_score_rate_available = serializers.BooleanField(required=False)
    kc_rows = serializers.ListField(child=serializers.DictField(), required=False)
    trend_rows = serializers.ListField(child=serializers.DictField(), required=False)
    trend_class_average_available = serializers.BooleanField(required=False)


class StudentLearningScoreSerializer(serializers.Serializer):
    generated_at = serializers.DateTimeField()
    value = serializers.DecimalField(max_digits=12, decimal_places=2)
    maximum = serializers.DecimalField(max_digits=12, decimal_places=2)


class StudentLearningItemResultSerializer(serializers.Serializer):
    generated_at = serializers.DateTimeField()
    question_id = serializers.IntegerField()
    code = serializers.CharField()
    kcs = serializers.ListField(child=serializers.CharField())
    result_label = serializers.CharField()
    result_state = serializers.ChoiceField(
        choices=("correct", "incorrect", "pending")
    )
    final_answer = serializers.CharField()
    score = StudentLearningScoreSerializer(required=False)


class StudentLearningExperimentSerializer(serializers.Serializer):
    version = serializers.CharField(required=False)
    comparison_status = serializers.ChoiceField(choices=("pending", "updating", "ready", "failed"), required=False)
    comparison_generated_at = serializers.DateTimeField(required=False)
    generated_at = serializers.DateTimeField()
    course_id = serializers.IntegerField()
    course_name = serializers.CharField()
    class_group_name = serializers.CharField()
    experiment_id = serializers.IntegerField()
    experiment_name = serializers.CharField()
    status = serializers.CharField()
    status_label = serializers.CharField()
    completed = serializers.BooleanField()
    participated = serializers.BooleanField()
    correct_rate = serializers.DictField(required=False)
    correct_rate_comparison = serializers.DictField(required=False)
    score_rate = serializers.DictField(required=False)
    grading_coverage = serializers.DictField(required=False)
    score_rate_comparison = serializers.DictField(required=False)
    rank = serializers.DictField(required=False)
    item_results = StudentLearningItemResultSerializer(many=True, required=False)


class StudentLearningQuestionOptionSerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField(allow_blank=True)
    text = serializers.CharField(allow_blank=True)
    selected_by_student = serializers.BooleanField()
    is_correct = serializers.BooleanField(required=False)


class StudentFeedbackCaptureAuthorizationSerializer(serializers.Serializer):
    correctness = serializers.BooleanField()
    correct_answer = serializers.BooleanField()
    explanation = serializers.BooleanField()


class StudentFeedbackCaptureSerializer(serializers.Serializer):
    capture_version = serializers.CharField()
    session_id = serializers.IntegerField()
    attempt_id = serializers.IntegerField()
    event_batch_url = serializers.CharField()
    checkpoint_url = serializers.CharField()
    time_sync_create_url = serializers.CharField()
    time_sync_finalize_url = serializers.CharField()
    app_version = serializers.CharField()
    heartbeat_interval_seconds = serializers.IntegerField()
    answer_event_id = serializers.UUIDField(allow_null=True)
    snapshot_id = serializers.UUIDField(allow_null=True)
    mode = serializers.ChoiceField(choices=("correctness", "full"))
    authorized = StudentFeedbackCaptureAuthorizationSerializer()


class StudentLearningQuestionSerializer(serializers.Serializer):
    version = serializers.CharField(required=False)
    generated_at = serializers.DateTimeField()
    course_id = serializers.IntegerField()
    course_name = serializers.CharField()
    class_group_name = serializers.CharField()
    experiment_id = serializers.IntegerField()
    experiment_name = serializers.CharField()
    question_id = serializers.IntegerField()
    code = serializers.CharField()
    question_type = serializers.CharField()
    text = serializers.CharField()
    kcs = serializers.ListField(child=serializers.CharField())
    result_label = serializers.CharField()
    result_state = serializers.ChoiceField(
        choices=("correct", "incorrect", "pending")
    )
    final_answer = serializers.CharField()
    options = StudentLearningQuestionOptionSerializer(many=True)
    feedback_capture = StudentFeedbackCaptureSerializer(required=False)
    score = StudentLearningScoreSerializer(required=False)
    correct_answer = serializers.CharField(required=False, allow_null=True)
    explanation = serializers.CharField(required=False, allow_blank=True)
