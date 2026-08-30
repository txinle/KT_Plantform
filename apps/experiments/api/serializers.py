from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.question_bank.models import Question, QuestionBank

from ..models import Experiment
from ..services import lock_experiment_question_bank, sync_experiment_questions


class QuestionBankReferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuestionBank
        fields = ("id", "name")
        read_only_fields = fields


class QuestionQuotaSerializer(serializers.Serializer):
    question_type = serializers.ChoiceField(choices=Question.Type.choices)
    amount = serializers.IntegerField(min_value=0)


class TeacherExperimentSerializer(serializers.ModelSerializer):
    question_bank = QuestionBankReferenceSerializer(read_only=True)
    question_bank_id = serializers.PrimaryKeyRelatedField(
        source="question_bank",
        queryset=QuestionBank.objects.all(),
        write_only=True,
    )
    question_quotas = QuestionQuotaSerializer(many=True, required=False)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    selection_mode_label = serializers.CharField(
        source="get_selection_mode_display", read_only=True
    )
    ordering_mode_label = serializers.CharField(
        source="get_ordering_mode_display", read_only=True
    )
    feedback_mode_label = serializers.CharField(
        source="get_feedback_mode_display", read_only=True
    )
    time_mode_label = serializers.CharField(
        source="get_time_mode_display", read_only=True
    )
    resume_mode_label = serializers.CharField(
        source="get_resume_mode_display", read_only=True
    )
    confidence_mode_label = serializers.CharField(
        source="get_confidence_mode_display", read_only=True
    )
    confidence_sampling_unit_label = serializers.CharField(
        source="get_confidence_sampling_unit_display", read_only=True
    )
    can_edit = serializers.SerializerMethodField()

    class Meta:
        model = Experiment
        fields = (
            "id",
            "name",
            "description",
            "question_bank",
            "question_bank_id",
            "status",
            "status_label",
            "selection_mode",
            "selection_mode_label",
            "question_quotas",
            "ordering_mode",
            "ordering_mode_label",
            "feedback_mode",
            "feedback_mode_label",
            "time_mode",
            "time_mode_label",
            "total_time_limit_minutes",
            "per_question_time_limit_seconds",
            "resume_mode",
            "resume_mode_label",
            "confidence_mode",
            "confidence_mode_label",
            "confidence_sampling_unit",
            "confidence_sampling_unit_label",
            "confidence_probability",
            "can_edit",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "status",
            "created_at",
            "updated_at",
        )

    def get_can_edit(self, experiment) -> bool:
        return experiment.status == Experiment.Status.DRAFT

    def validate_question_bank_id(self, bank):
        course = self.context["course"]
        if bank.course_id != course.pk:
            raise serializers.ValidationError("实验只能使用同一课程的题库。")
        if bank.visibility == QuestionBank.Visibility.ARCHIVED:
            raise serializers.ValidationError("归档题库不能用于实验。")
        return bank

    def validate_question_quotas(self, quotas):
        question_types = [quota["question_type"] for quota in quotas]
        if len(question_types) != len(set(question_types)):
            raise serializers.ValidationError("同一题型只能设置一个配额。")
        return quotas

    def _effective_value(self, attrs, field_name):
        if field_name in attrs:
            return attrs[field_name]
        if self.instance is not None:
            return getattr(self.instance, field_name)
        return Experiment._meta.get_field(field_name).get_default()

    def validate(self, attrs):
        attrs = super().validate(attrs)
        selection_mode = self._effective_value(attrs, "selection_mode")
        quotas = attrs.get("question_quotas")
        if quotas is None and self.instance is not None:
            quota_total = sum(
                self.instance.question_quotas.values_list("amount", flat=True)
            )
        else:
            quota_total = sum(quota["amount"] for quota in (quotas or []))
        if selection_mode == Experiment.SelectionMode.QUOTA and quota_total <= 0:
            raise serializers.ValidationError(
                {"question_quotas": "按题型配额抽题至少需要抽取 1 道题。"}
            )

        time_mode = self._effective_value(attrs, "time_mode")
        total_limit = self._effective_value(attrs, "total_time_limit_minutes")
        question_limit = self._effective_value(
            attrs, "per_question_time_limit_seconds"
        )
        if time_mode == Experiment.TimeMode.TOTAL and not total_limit:
            raise serializers.ValidationError(
                {"total_time_limit_minutes": "总时限模式必须填写分钟数。"}
            )
        if time_mode == Experiment.TimeMode.PER_QUESTION and not question_limit:
            raise serializers.ValidationError(
                {"per_question_time_limit_seconds": "单题时限模式必须填写秒数。"}
            )
        if time_mode == Experiment.TimeMode.NONE:
            attrs["total_time_limit_minutes"] = None
            attrs["per_question_time_limit_seconds"] = None
        elif time_mode == Experiment.TimeMode.TOTAL:
            attrs["per_question_time_limit_seconds"] = None
        else:
            attrs["total_time_limit_minutes"] = None

        confidence_mode = self._effective_value(attrs, "confidence_mode")
        probability = self._effective_value(attrs, "confidence_probability")
        if confidence_mode == Experiment.ConfidenceMode.SAMPLED:
            if probability is None:
                raise serializers.ValidationError(
                    {"confidence_probability": "抽样采集必须设置抽样概率。"}
                )
        else:
            attrs["confidence_probability"] = None
        return attrs

    def _save_quotas(self, experiment, quotas):
        if quotas is None:
            return
        amounts = {quota["question_type"]: quota["amount"] for quota in quotas}
        for question_type in Question.Type.values:
            experiment.question_quotas.update_or_create(
                question_type=question_type,
                defaults={"amount": amounts.get(question_type, 0)},
            )

    def create(self, validated_data):
        quotas = validated_data.pop("question_quotas", None)
        validated_data["question_bank"] = lock_experiment_question_bank(
            validated_data["question_bank"], validated_data["course"]
        )
        experiment = Experiment(**validated_data)
        try:
            experiment.full_clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict) from exc
        experiment.save()
        self._save_quotas(experiment, quotas)
        sync_experiment_questions(experiment)
        return experiment

    def update(self, instance, validated_data):
        quotas = validated_data.pop("question_quotas", None)
        original_bank_id = instance.question_bank_id
        if "question_bank" in validated_data:
            validated_data["question_bank"] = lock_experiment_question_bank(
                validated_data["question_bank"], instance.course
            )
        for field_name, value in validated_data.items():
            setattr(instance, field_name, value)
        try:
            instance.full_clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict) from exc
        instance.save()
        self._save_quotas(instance, quotas)
        if instance.question_bank_id != original_bank_id:
            sync_experiment_questions(instance)
        return instance


class PageMetadataSerializer(serializers.Serializer):
    number = serializers.IntegerField()
    size = serializers.IntegerField()
    total_items = serializers.IntegerField()
    total_pages = serializers.IntegerField()
    start = serializers.IntegerField()
    end = serializers.IntegerField()


class TeacherExperimentClassOptionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    academic_year = serializers.CharField()
    student_count = serializers.IntegerField(min_value=0)


class TeacherExperimentSummarySerializer(serializers.Serializer):
    question_banks = QuestionBankReferenceSerializer(many=True)
    classes = TeacherExperimentClassOptionSerializer(many=True)


class TeacherExperimentCollectionSerializer(serializers.Serializer):
    items = TeacherExperimentSerializer(many=True)
    page = PageMetadataSerializer()
    summary = TeacherExperimentSummarySerializer()


class TeacherExperimentReadinessSerializer(serializers.Serializer):
    ready = serializers.BooleanField()
    issues = serializers.ListField(child=serializers.CharField())
    candidate_count = serializers.IntegerField(min_value=0)
    participant_count = serializers.IntegerField(min_value=0)


class PublishPlanClassSerializer(serializers.Serializer):
    class_id = serializers.IntegerField()
    class_name = serializers.CharField()
    academic_year = serializers.CharField()
    excluded_count = serializers.IntegerField(min_value=0)
    participant_count = serializers.IntegerField(min_value=0)
    schedule_configured = serializers.BooleanField()
    schedule_mode = serializers.ChoiceField(choices=("default", "custom", "unset"))
    opens_at = serializers.DateTimeField(allow_null=True)
    closes_at = serializers.DateTimeField(allow_null=True)


class PublishPlanSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    experiment_id = serializers.IntegerField()
    status = serializers.CharField()
    current_step = serializers.IntegerField(min_value=1, max_value=2)
    setup_status = serializers.ChoiceField(choices=("empty", "incomplete", "complete"))
    missing_requirements = serializers.ListField(child=serializers.CharField())
    field_errors = serializers.DictField()
    plan_revision = serializers.IntegerField(min_value=1)
    expires_at = serializers.DateTimeField()
    default_opens_at = serializers.DateTimeField(allow_null=True)
    default_closes_at = serializers.DateTimeField(allow_null=True)
    classes = PublishPlanClassSerializer(many=True)


class PublishPlanClassSelectionSerializer(serializers.Serializer):
    class_id = serializers.IntegerField(min_value=1)
    excluded_student_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=True,
    )

    def validate_excluded_student_ids(self, student_ids):
        if len(student_ids) != len(set(student_ids)):
            raise serializers.ValidationError("排除学生不能重复。")
        return student_ids


class PublishPlanDefaultScheduleSerializer(serializers.Serializer):
    opens_at = serializers.DateTimeField(allow_null=True)
    closes_at = serializers.DateTimeField(allow_null=True)

    def validate(self, attrs):
        if (
            attrs["opens_at"] is not None
            and attrs["closes_at"] is not None
            and attrs["closes_at"] <= attrs["opens_at"]
        ):
            raise serializers.ValidationError("默认截止时间必须晚于开始时间。")
        return attrs


class PublishPlanScheduleSerializer(serializers.Serializer):
    class_id = serializers.IntegerField(min_value=1)
    mode = serializers.ChoiceField(choices=("default", "custom"))
    opens_at = serializers.DateTimeField(allow_null=True, required=False, default=None)
    closes_at = serializers.DateTimeField(allow_null=True, required=False, default=None)

    def validate(self, attrs):
        if attrs["mode"] == "default" and (
            attrs["opens_at"] is not None or attrs["closes_at"] is not None
        ):
            raise serializers.ValidationError("默认排期不能同时提交班级时间。")
        if (
            attrs["opens_at"] is not None
            and attrs["closes_at"] is not None
            and attrs["closes_at"] <= attrs["opens_at"]
        ):
            raise serializers.ValidationError("班级截止时间必须晚于开始时间。")
        return attrs


class PublishPlanSetupInputSerializer(serializers.Serializer):
    expected_revision = serializers.IntegerField(min_value=1)
    classes = PublishPlanClassSelectionSerializer(many=True)
    default_schedule = PublishPlanDefaultScheduleSerializer()
    schedules = PublishPlanScheduleSerializer(many=True)

    def validate(self, attrs):
        class_ids = [item["class_id"] for item in attrs["classes"]]
        schedule_ids = [item["class_id"] for item in attrs["schedules"]]
        if len(class_ids) != len(set(class_ids)):
            raise serializers.ValidationError({"classes": "班级不能重复。"})
        if len(schedule_ids) != len(set(schedule_ids)):
            raise serializers.ValidationError({"schedules": "班级排期不能重复。"})
        if not set(schedule_ids).issubset(class_ids):
            raise serializers.ValidationError({"schedules": "排期包含未选择的班级。"})
        return attrs


class ExpectedRevisionSerializer(serializers.Serializer):
    expected_revision = serializers.IntegerField(min_value=1)


class PublishPreviewSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    plan_revision = serializers.IntegerField(min_value=1)
    class_count = serializers.IntegerField(min_value=0)
    participant_count = serializers.IntegerField(min_value=0)
    item_count = serializers.IntegerField(min_value=0)
    randomization_validation = serializers.CharField()
    created_at = serializers.DateTimeField()


class PublishPlanReviewSerializer(serializers.Serializer):
    plan = PublishPlanSerializer()
    preview = PublishPreviewSummarySerializer()
    effects = serializers.ListField(child=serializers.CharField())


class PublishedTaskSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    class_id = serializers.IntegerField()
    class_name = serializers.CharField()
    business_status = serializers.CharField()
    opens_at = serializers.DateTimeField(allow_null=True)
    closes_at = serializers.DateTimeField(allow_null=True)


class PublishCommitSerializer(serializers.Serializer):
    tasks = PublishedTaskSerializer(many=True)


class PublishParticipantSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    student_number = serializers.CharField()
    display_name = serializers.CharField()
    class_id = serializers.IntegerField()
    class_name = serializers.CharField()
    item_count = serializers.IntegerField(min_value=0)
    is_valid = serializers.BooleanField()
    excluded = serializers.BooleanField()


class PublishParticipantCollectionSerializer(serializers.Serializer):
    items = PublishParticipantSerializer(many=True)
    page = PageMetadataSerializer()


class PublishParticipantItemSerializer(serializers.Serializer):
    assigned_sequence_index = serializers.IntegerField(min_value=1)
    question_id = serializers.IntegerField()
    question_code = serializers.CharField()
    question_type = serializers.CharField()


class PublishParticipantDetailSerializer(PublishParticipantSerializer):
    items = PublishParticipantItemSerializer(many=True)


class ClassTaskSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    experiment_id = serializers.IntegerField()
    experiment_name = serializers.CharField()
    class_id = serializers.IntegerField()
    class_name = serializers.CharField()
    business_status = serializers.CharField()
    published_at = serializers.DateTimeField()
    opens_at = serializers.DateTimeField(allow_null=True)
    closes_at = serializers.DateTimeField(allow_null=True)
    schedule_revision = serializers.IntegerField(min_value=1)
    participant_count = serializers.IntegerField(min_value=0)
    available_actions = serializers.ListField(child=serializers.CharField())


class ClassTaskCollectionSerializer(serializers.Serializer):
    items = ClassTaskSerializer(many=True)


class ClassTaskProgressSerializer(serializers.Serializer):
    task_id = serializers.IntegerField()
    total_participants = serializers.IntegerField(min_value=0)
    not_started = serializers.IntegerField(min_value=0)
    in_progress = serializers.IntegerField(min_value=0)
    completed = serializers.IntegerField(min_value=0)
    expired = serializers.IntegerField(min_value=0)
    invalidated = serializers.IntegerField(min_value=0)


class ScheduleRevisionInputSerializer(serializers.Serializer):
    opens_at = serializers.DateTimeField(allow_null=True)
    closes_at = serializers.DateTimeField(allow_null=True)
    reason = serializers.CharField(max_length=500, allow_blank=True, required=False)

    def validate(self, attrs):
        if (
            attrs["opens_at"] is not None
            and attrs["closes_at"] is not None
            and attrs["closes_at"] <= attrs["opens_at"]
        ):
            raise serializers.ValidationError("截止时间必须晚于开始时间。")
        return attrs


class TaskReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500, allow_blank=True, required=False)
