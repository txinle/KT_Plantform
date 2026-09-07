from rest_framework import serializers

from apps.question_bank.models import Question

from ..models import Experiment


class StandaloneChoiceSerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()


class StandaloneQuestionBankSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class StandaloneQuestionQuotaInputSerializer(serializers.Serializer):
    question_type = serializers.ChoiceField(choices=Question.Type.choices)
    amount = serializers.IntegerField(min_value=0)


class StandaloneExperimentInputSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=180, required=False)
    description = serializers.CharField(allow_blank=True, required=False)
    question_bank_id = serializers.IntegerField(min_value=1, required=False)
    selection_mode = serializers.ChoiceField(
        choices=Experiment.SelectionMode.choices,
        required=False,
    )
    question_quotas = StandaloneQuestionQuotaInputSerializer(
        many=True,
        required=False,
    )
    ordering_mode = serializers.ChoiceField(
        choices=Experiment.Ordering.choices,
        required=False,
    )
    feedback_mode = serializers.ChoiceField(
        choices=Experiment.Feedback.choices,
        required=False,
    )
    feedback_release_policy = serializers.ChoiceField(
        choices=Experiment.FeedbackReleasePolicy.choices,
        required=False,
    )
    time_mode = serializers.ChoiceField(
        choices=Experiment.TimeMode.choices,
        required=False,
    )
    total_time_limit_minutes = serializers.IntegerField(
        min_value=1,
        allow_null=True,
        required=False,
    )
    per_question_time_limit_seconds = serializers.IntegerField(
        min_value=1,
        allow_null=True,
        required=False,
    )
    resume_mode = serializers.ChoiceField(
        choices=Experiment.ResumeMode.choices,
        required=False,
    )
    confidence_mode = serializers.ChoiceField(
        choices=Experiment.ConfidenceMode.choices,
        required=False,
    )
    confidence_sampling_unit = serializers.ChoiceField(
        choices=Experiment.ConfidenceSamplingUnit.choices,
        required=False,
    )
    confidence_probability = serializers.DecimalField(
        max_digits=4,
        decimal_places=3,
        min_value=0,
        max_value=1,
        allow_null=True,
        required=False,
    )
    confidence_randomization_seed = serializers.CharField(
        max_length=128,
        allow_blank=True,
        required=False,
    )
    confidence_assignment_version = serializers.CharField(
        max_length=64,
        required=False,
    )
    confidence_scale_version = serializers.CharField(
        max_length=64,
        required=False,
    )
    confidence_prompt_version = serializers.CharField(
        max_length=64,
        required=False,
    )
    heartbeat_interval_seconds = serializers.IntegerField(
        min_value=15,
        max_value=120,
        required=False,
    )

    def validate_question_quotas(self, quotas):
        question_types = [row["question_type"] for row in quotas]
        if len(question_types) != len(set(question_types)):
            raise serializers.ValidationError("同一题型只能设置一个配额。")
        return quotas


class StandaloneExperimentListItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    description = serializers.CharField()
    question_bank = StandaloneQuestionBankSerializer()
    ordering_mode_label = serializers.CharField()
    student_count = serializers.IntegerField(min_value=0)
    status = serializers.CharField()
    status_label = serializers.CharField()
    updated_at = serializers.DateTimeField()
    detail_url = serializers.CharField()


class StandaloneExperimentCollectionSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=("standalone", "canonical"))
    canonical_url = serializers.CharField(allow_null=True, required=False)
    items = StandaloneExperimentListItemSerializer(many=True, required=False)
    question_banks = StandaloneQuestionBankSerializer(many=True, required=False)
    choices = serializers.DictField(required=False)


class StandaloneStudentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    student_number = serializers.CharField(allow_null=True)
    display_name = serializers.CharField()
    class_name = serializers.CharField()
    selected = serializers.BooleanField()


class StandaloneInventoryRowSerializer(serializers.Serializer):
    label = serializers.CharField()
    need = serializers.IntegerField(min_value=0)
    available = serializers.IntegerField(min_value=0)


class StandaloneVersionSerializer(serializers.Serializer):
    version_number = serializers.IntegerField(min_value=1)
    published_at = serializers.DateTimeField()
    candidate_count = serializers.IntegerField(min_value=0)
    inventory_rows = StandaloneInventoryRowSerializer(many=True)


class StandaloneExperimentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    description = serializers.CharField()
    question_bank = StandaloneQuestionBankSerializer()
    status = serializers.CharField()
    status_label = serializers.CharField()
    selection_mode = serializers.CharField()
    selection_mode_label = serializers.CharField()
    ordering_mode = serializers.CharField()
    ordering_mode_label = serializers.CharField()
    feedback_mode = serializers.CharField()
    feedback_mode_label = serializers.CharField()
    feedback_release_policy = serializers.CharField()
    feedback_release_policy_label = serializers.CharField()
    time_mode = serializers.CharField()
    time_mode_label = serializers.CharField()
    total_time_limit_minutes = serializers.IntegerField(allow_null=True)
    per_question_time_limit_seconds = serializers.IntegerField(allow_null=True)
    resume_mode = serializers.CharField()
    resume_mode_label = serializers.CharField()
    confidence_mode = serializers.CharField()
    confidence_mode_label = serializers.CharField()
    confidence_sampling_unit = serializers.CharField()
    confidence_sampling_unit_label = serializers.CharField()
    confidence_probability = serializers.DecimalField(
        max_digits=4,
        decimal_places=3,
        allow_null=True,
    )
    confidence_randomization_seed = serializers.CharField(allow_blank=True)
    confidence_assignment_version = serializers.CharField()
    confidence_scale_version = serializers.CharField()
    confidence_prompt_version = serializers.CharField()
    heartbeat_interval_seconds = serializers.IntegerField(min_value=15)
    question_count = serializers.IntegerField(min_value=0)
    student_count = serializers.IntegerField(min_value=0)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class StandaloneExperimentDetailSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=("standalone", "canonical"))
    canonical_url = serializers.CharField(allow_null=True, required=False)
    experiment = StandaloneExperimentSerializer(required=False)
    selection = serializers.DictField(required=False)
    students = StandaloneStudentSerializer(many=True, required=False)
    available_students = StandaloneStudentSerializer(many=True, required=False)
    question_banks = StandaloneQuestionBankSerializer(many=True, required=False)
    actions = serializers.DictField(required=False)
    choices = serializers.DictField(required=False)


class StandaloneActionInputSerializer(serializers.Serializer):
    confirmation = serializers.CharField(required=False, allow_blank=True)


class StandaloneStudentAssignmentInputSerializer(serializers.Serializer):
    student_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=True,
    )

    def validate_student_ids(self, student_ids):
        if len(student_ids) != len(set(student_ids)):
            raise serializers.ValidationError("同一学生只能选择一次。")
        return student_ids


class StandaloneSessionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    student_number = serializers.CharField(allow_null=True)
    student_name = serializers.CharField()
    status = serializers.CharField()
    status_label = serializers.CharField()
    started_at = serializers.DateTimeField(allow_null=True)
    submission_count = serializers.IntegerField(min_value=0)


class StandaloneExperimentResultsSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=("standalone", "canonical"))
    canonical_url = serializers.CharField(allow_null=True, required=False)
    experiment = StandaloneExperimentSerializer(required=False)
    sessions = StandaloneSessionSerializer(many=True, required=False)
    session_status_choices = StandaloneChoiceSerializer(many=True, required=False)
    selected_status = serializers.CharField(required=False)
    export_field_groups = serializers.ListField(required=False)
    actions = serializers.DictField(required=False)
