from rest_framework import serializers

from .serializers import CourseReferenceSerializer, MetricValueSerializer


class TeacherAnalysisClassGroupSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    academic_year = serializers.CharField()


class StudentExperimentPerformanceSerializer(serializers.Serializer):
    experiment_id = serializers.IntegerField()
    name = serializers.CharField()
    completed = serializers.BooleanField()
    correct_rate = MetricValueSerializer()
    score_rate = MetricValueSerializer()
    rank = serializers.IntegerField(allow_null=True, min_value=1)
    rank_sample_size = serializers.IntegerField(min_value=0)
    average_answer_time_ms = serializers.DecimalField(
        max_digits=20,
        decimal_places=3,
        allow_null=True,
    )
    average_answer_time_display = serializers.CharField()
    detail_url = serializers.CharField()


class StudentTrendSerializer(serializers.Serializer):
    experiment_id = serializers.IntegerField()
    name = serializers.CharField()
    student_correct_rate = MetricValueSerializer()
    class_average_correct_rate = MetricValueSerializer()
    x = serializers.DecimalField(max_digits=20, decimal_places=8)
    student_y = serializers.DecimalField(max_digits=20, decimal_places=8)
    class_y = serializers.DecimalField(max_digits=20, decimal_places=8)


class StudentComparisonSerializer(serializers.Serializer):
    student_value = MetricValueSerializer()
    class_average = MetricValueSerializer()
    sample_size = serializers.IntegerField(min_value=0)


class StudentKCComparisonSerializer(serializers.Serializer):
    name = serializers.CharField()
    comparison = StudentComparisonSerializer()


class StudentDetailSerializer(serializers.Serializer):
    generated_at = serializers.DateTimeField()
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
    experiments = StudentExperimentPerformanceSerializer(many=True)
    trend_rows = StudentTrendSerializer(many=True)
    show_trend_chart = serializers.BooleanField()
    kc_rows = StudentKCComparisonSerializer(many=True)


class StudentDetailActionsSerializer(serializers.Serializer):
    back_url = serializers.CharField()


class StudentDetailResponseSerializer(serializers.Serializer):
    course = CourseReferenceSerializer()
    class_group = TeacherAnalysisClassGroupSerializer()
    detail = StudentDetailSerializer()
    actions = StudentDetailActionsSerializer()


class StudentExperimentQuestionSerializer(serializers.Serializer):
    question_id = serializers.IntegerField()
    code = serializers.CharField()
    question_summary = serializers.CharField()
    kcs = serializers.ListField(child=serializers.CharField())
    result_label = serializers.CharField()
    score_display = serializers.CharField()
    first_answer = serializers.CharField(allow_null=True)
    final_answer = serializers.CharField()
    revision_count = serializers.IntegerField(allow_null=True, min_value=0)
    answer_time_ms = serializers.DecimalField(
        max_digits=20,
        decimal_places=3,
        allow_null=True,
    )
    answer_time_display = serializers.CharField()
    process_complete = serializers.BooleanField()
    confidence_label = serializers.CharField()
    submission_status_label = serializers.CharField()
    submitted_at = serializers.DateTimeField()
    question_url = serializers.CharField()
    process_url = serializers.CharField()


class StudentExperimentDetailSerializer(serializers.Serializer):
    generated_at = serializers.DateTimeField()
    experiment_id = serializers.IntegerField()
    experiment_name = serializers.CharField()
    student_id = serializers.IntegerField()
    student_number = serializers.CharField()
    display_name = serializers.CharField()
    is_current_member = serializers.BooleanField()
    completed = serializers.BooleanField()
    correct_rate = MetricValueSerializer()
    score_rate = MetricValueSerializer()
    average_answer_time_ms = serializers.DecimalField(
        max_digits=20,
        decimal_places=3,
        allow_null=True,
    )
    average_answer_time_display = serializers.CharField()
    rank = serializers.IntegerField(allow_null=True, min_value=1)
    rank_sample_size = serializers.IntegerField(min_value=0)
    questions = StudentExperimentQuestionSerializer(many=True)


class StudentExperimentDetailActionsSerializer(serializers.Serializer):
    back_url = serializers.CharField()


class StudentExperimentDetailResponseSerializer(serializers.Serializer):
    course = CourseReferenceSerializer()
    class_group = TeacherAnalysisClassGroupSerializer()
    detail = StudentExperimentDetailSerializer()
    actions = StudentExperimentDetailActionsSerializer()


class TimingQualitySerializer(serializers.Serializer):
    submitted_count = serializers.IntegerField(min_value=0)
    exact_count = serializers.IntegerField(min_value=0)
    estimated_count = serializers.IntegerField(min_value=0)
    wall_clock_count = serializers.IntegerField(min_value=0)
    unavailable_count = serializers.IntegerField(min_value=0)


class QuestionTimingSerializer(serializers.Serializer):
    median_display = serializers.CharField()
    iqr_display = serializers.CharField()
    sample_size = serializers.IntegerField(min_value=0)
    quality = TimingQualitySerializer()


class OptionDistributionSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    count = serializers.IntegerField(min_value=0)
    is_correct = serializers.BooleanField()


class TransitionSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    count = serializers.IntegerField(min_value=0)


class QuestionParticipantSerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    student_number = serializers.CharField()
    display_name = serializers.CharField()
    is_current_member = serializers.BooleanField()
    result_label = serializers.CharField()
    final_answer = serializers.CharField()
    process_complete = serializers.BooleanField()
    student_experiment_url = serializers.CharField()
    process_url = serializers.CharField()


class QuestionDetailSerializer(serializers.Serializer):
    generated_at = serializers.DateTimeField()
    experiment_id = serializers.IntegerField()
    experiment_name = serializers.CharField()
    question_id = serializers.IntegerField()
    code = serializers.CharField()
    text = serializers.CharField()
    question_type = serializers.CharField()
    kcs = serializers.ListField(child=serializers.CharField())
    eligible_count = serializers.IntegerField(min_value=0)
    current_member_count = serializers.IntegerField(min_value=0)
    historical_participant_count = serializers.IntegerField(min_value=0)
    answer_count = serializers.IntegerField(min_value=0)
    correct_count = serializers.IntegerField(min_value=0)
    incorrect_count = serializers.IntegerField(min_value=0)
    correct_rate = MetricValueSerializer()
    timing = QuestionTimingSerializer()
    chain_coverage = MetricValueSerializer()
    option_rows = OptionDistributionSerializer(many=True)
    transition_applicability = serializers.ChoiceField(
        choices=("applicable", "not_applicable")
    )
    transition_rows = TransitionSerializer(many=True)
    participants = QuestionParticipantSerializer(many=True)


class QuestionDetailActionsSerializer(serializers.Serializer):
    back_url = serializers.CharField()


class QuestionDetailResponseSerializer(serializers.Serializer):
    course = CourseReferenceSerializer()
    class_group = TeacherAnalysisClassGroupSerializer()
    detail = QuestionDetailSerializer()
    actions = QuestionDetailActionsSerializer()


class ProcessStepSerializer(serializers.Serializer):
    label = serializers.CharField()
    occurred_at = serializers.DateTimeField(allow_null=True)
    value = serializers.CharField(allow_null=True)
    value_before = serializers.CharField(allow_null=True)
    value_after = serializers.CharField(allow_null=True)
    length_before = serializers.IntegerField(allow_null=True, min_value=0)
    length_after = serializers.IntegerField(allow_null=True, min_value=0)
    time_source = serializers.CharField()
    interval_ms = serializers.IntegerField(allow_null=True, min_value=0)


class AttemptProcessSerializer(serializers.Serializer):
    question_type = serializers.CharField()
    completeness = serializers.ChoiceField(choices=("complete", "incomplete"))
    completeness_label = serializers.CharField()
    steps = ProcessStepSerializer(many=True)
    first_value = serializers.CharField(allow_null=True)
    final_value = serializers.CharField(allow_null=True)
    revision_count = serializers.IntegerField(allow_null=True, min_value=0)


class AttemptProcessDetailSerializer(serializers.Serializer):
    generated_at = serializers.DateTimeField()
    experiment_id = serializers.IntegerField()
    experiment_name = serializers.CharField()
    question_id = serializers.IntegerField()
    question_code = serializers.CharField()
    question_text = serializers.CharField()
    question_options = serializers.ListField(child=serializers.CharField())
    correct_answer = serializers.CharField(allow_blank=True)
    student_id = serializers.IntegerField()
    student_number = serializers.CharField()
    display_name = serializers.CharField()
    is_current_member = serializers.BooleanField()
    result_label = serializers.CharField()
    score_display = serializers.CharField()
    confidence_label = serializers.CharField()
    answer_time_display = serializers.CharField()
    process = AttemptProcessSerializer()
    coverage_status_title = serializers.CharField()
    coverage_status_description = serializers.CharField()
    coverage_time_title = serializers.CharField()
    coverage_time_description = serializers.CharField()
    coverage_reason_labels = serializers.ListField(child=serializers.CharField())
    has_cross_instance_order_uncertainty = serializers.BooleanField()


class AttemptProcessActionsSerializer(serializers.Serializer):
    back_url = serializers.CharField()


class AttemptProcessResponseSerializer(serializers.Serializer):
    course = CourseReferenceSerializer()
    class_group = TeacherAnalysisClassGroupSerializer()
    detail = AttemptProcessDetailSerializer()
    actions = AttemptProcessActionsSerializer()
