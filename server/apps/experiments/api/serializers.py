from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field

from apps.api.exceptions import ApiConflict
from apps.question_bank.models import Concept, Question, QuestionBank

from ..collection_status import COLLECTION_STATUS_CHOICES
from ..models import Experiment, ExperimentSamplingKC
from ..sampling import SamplingService, SamplingValidationError
from ..services import sync_experiment_questions
from ..question_bank_selection import validate_question_banks, replace_question_banks


class QuestionBankReferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuestionBank
        fields = ("id", "name")
        read_only_fields = fields


class QuestionQuotaSerializer(serializers.Serializer):
    question_type = serializers.ChoiceField(choices=Question.Type.choices)
    amount = serializers.IntegerField(min_value=0)


class SamplingQuestionTypeQuotaSerializer(serializers.Serializer):
    question_type = serializers.ChoiceField(choices=Question.Type.choices)
    amount = serializers.IntegerField(min_value=1)


class SamplingKCSerializer(serializers.Serializer):
    kc_code = serializers.CharField(max_length=32)
    kc_name = serializers.CharField(source="concept.name", read_only=True)
    quota = serializers.IntegerField(min_value=1, allow_null=True, required=False)
    match_scope = serializers.ChoiceField(choices=Experiment.MatchScope.choices)

    def to_representation(self, instance):
        if isinstance(instance, dict):
            return super().to_representation(instance)
        return {
            "kc_code": instance.concept.code,
            "kc_name": instance.concept.name,
            "quota": instance.quota,
            "match_scope": instance.match_scope,
        }


class SamplingDefinitionSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(
        source="sampling_mode",
        choices=Experiment.SamplingMode.choices,
        allow_null=True,
    )
    total_count = serializers.IntegerField(
        source="sampling_total_count",
        min_value=1,
        allow_null=True,
        required=False,
    )
    match_scope = serializers.ChoiceField(
        source="sampling_match_scope",
        choices=Experiment.MatchScope.choices,
        required=False,
    )
    nodes = SamplingKCSerializer(source="sampling_kcs", many=True, required=False)
    type_quotas = SamplingQuestionTypeQuotaSerializer(many=True, required=False)

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        amounts = {
            row.question_type: row.amount
            for row in instance.question_quotas.all()
            if row.amount > 0
        }
        representation["type_quotas"] = [
            {"question_type": question_type, "amount": amounts[question_type]}
            for question_type in Question.Type.values
            if question_type in amounts
        ]
        return representation


class TeacherExperimentSerializer(serializers.ModelSerializer):
    cross_experiment_item_policy = serializers.ChoiceField(
        choices=Experiment.CrossExperimentItemPolicy.choices,
        default=Experiment.CrossExperimentItemPolicy.EXCLUDE_PREVIOUS,
    )
    question_bank = QuestionBankReferenceSerializer(read_only=True, allow_null=True)
    question_bank_id = serializers.PrimaryKeyRelatedField(
        source="question_bank",
        queryset=QuestionBank.objects.all(),
        write_only=True,
        required=False,
    )
    question_bank_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, source="selected_question_bank_ids"
    )
    question_banks = serializers.SerializerMethodField()
    question_quotas = QuestionQuotaSerializer(many=True, read_only=True)
    sampling_kcs = SamplingKCSerializer(many=True, read_only=True)
    sampling = SamplingDefinitionSerializer(source="*", required=False)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    selection_mode_label = serializers.CharField(
        source="get_selection_mode_display", read_only=True
    )
    sampling_mode_label = serializers.CharField(
        source="get_sampling_mode_display",
        read_only=True,
        allow_null=True,
    )
    ordering_mode_label = serializers.CharField(
        source="get_ordering_mode_display", read_only=True
    )
    feedback_mode_label = serializers.CharField(
        source="get_feedback_mode_display", read_only=True
    )
    feedback_release_policy_label = serializers.CharField(
        source="get_feedback_release_policy_display", read_only=True
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
    can_manage = serializers.SerializerMethodField()

    class Meta:
        model = Experiment
        fields = (
            "id",
            "name",
            "description",
            "question_bank",
            "question_bank_id",
            "question_bank_ids",
            "question_banks",
            "status",
            "status_label",
            "selection_mode",
            "selection_mode_label",
            "question_quotas",
            "sampling_mode",
            "sampling_mode_label",
            "sampling_total_count",
            "sampling_match_scope",
            "sampling_kcs",
            "sampling",
            "cross_experiment_item_policy",
            "review_count",
            "ordering_mode",
            "ordering_mode_label",
            "feedback_mode",
            "feedback_release_policy",
            "research_tools",
            "feedback_mode_label",
            "feedback_release_policy_label",
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
            "can_manage",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "status",
            "created_at",
            "updated_at",
            "selection_mode",
            "sampling_mode",
            "sampling_total_count",
            "sampling_match_scope",
            "sampling_kcs",
        )

    def get_can_edit(self, experiment) -> bool:
        return experiment.status == Experiment.Status.DRAFT

    def get_can_manage(self, experiment) -> bool:
        request = self.context.get("request")
        return bool(request and experiment.course and experiment.course.can_manage(request.user)
                    and experiment.course.status == experiment.course.Status.ACTIVE)

    @extend_schema_field(QuestionBankReferenceSerializer(many=True))
    def get_question_banks(self, experiment):
        return QuestionBankReferenceSerializer(experiment.selected_question_banks, many=True).data

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation["question_bank_ids"] = instance.selected_question_bank_ids
        if not instance.frozen_release_snapshot_id:
            return representation
        configuration = instance.frozen_release_snapshot.configuration
        if "question_banks" in configuration:
            representation["question_banks"] = configuration["question_banks"]
            representation["question_bank_ids"] = configuration["question_bank_ids"]
            representation["question_bank"] = next(iter(configuration["question_banks"]), None)
        frozen_fields = (
            "research_tools",
            "selection_mode",
            "sampling_mode",
            "sampling_total_count",
            "cross_experiment_item_policy",
            "review_count",
            "ordering_mode",
            "feedback_mode",
            "feedback_release_policy",
            "time_mode",
            "total_time_limit_minutes",
            "per_question_time_limit_seconds",
            "resume_mode",
            "confidence_mode",
            "confidence_sampling_unit",
            "confidence_probability",
        )
        for field_name in frozen_fields:
            if field_name in configuration:
                value = configuration[field_name]
                representation[field_name] = None if value == "" else value
        representation["question_quotas"] = [
            {
                "question_type": question_type,
                "amount": configuration.get("quota_map", {}).get(question_type, 0),
            }
            for question_type in Question.Type.values
        ]
        representation["sampling_kcs"] = [
            {
                "kc_code": row["kc_code"],
                "kc_name": row["kc_name"],
                "quota": row.get("quota"),
                "match_scope": row["match_scope"],
            }
            for row in configuration.get("sampling_kcs", [])
        ]
        representation["sampling"] = {
            "mode": representation["sampling_mode"],
            "total_count": representation["sampling_total_count"],
            "match_scope": representation["sampling_match_scope"],
            "nodes": representation["sampling_kcs"],
            "type_quotas": configuration.get("sampling_type_quotas", []),
        }
        label_fields = {
            "selection_mode": Experiment.SelectionMode,
            "sampling_mode": Experiment.SamplingMode,
            "cross_experiment_item_policy": Experiment.CrossExperimentItemPolicy,
            "ordering_mode": Experiment.Ordering,
            "feedback_mode": Experiment.Feedback,
            "feedback_release_policy": Experiment.FeedbackReleasePolicy,
            "time_mode": Experiment.TimeMode,
            "resume_mode": Experiment.ResumeMode,
            "confidence_mode": Experiment.ConfidenceMode,
            "confidence_sampling_unit": Experiment.ConfidenceSamplingUnit,
        }
        for field_name, choices in label_fields.items():
            value = representation.get(field_name)
            representation[f"{field_name}_label"] = dict(choices.choices).get(
                value,
                representation.get(f"{field_name}_label"),
            )
        return representation

    def validate_question_bank_id(self, bank):
        course = self.context["course"]
        if bank.course_id != course.pk:
            raise serializers.ValidationError("实验只能使用同一课程的题库。")
        if bank.visibility == QuestionBank.Visibility.ARCHIVED:
            raise serializers.ValidationError("归档题库不能用于实验。")
        return bank

    def validate_research_tools(self, value):
        from apps.experiments.research_tool_config import validate_research_tools
        try:
            return validate_research_tools(value)
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.messages) from error

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
        ids = attrs.pop("selected_question_bank_ids", None)
        legacy_bank = attrs.pop("question_bank", None)
        if ids is not None and legacy_bank is not None and ids != [legacy_bank.pk]:
            raise serializers.ValidationError({"question_bank_ids": "请勿同时提交不一致的单题库和多题库选择。"})
        if ids is None and legacy_bank is not None:
            ids = [legacy_bank.pk]
        if ids is not None or self.instance is None:
            try:
                banks = validate_question_banks(self.context["course"], ids or [])
            except DjangoValidationError as exc:
                raise serializers.ValidationError(exc.message_dict) from exc
            attrs["selected_banks"] = banks
        legacy_fields = {
            field_name
            for field_name in ("selection_mode", "question_quotas")
            if field_name in self.initial_data
        }
        if legacy_fields:
            raise serializers.ValidationError(
                {
                    field_name: "旧版全量/题型配额仅支持读取，不能用于新配置。"
                    for field_name in legacy_fields
                }
            )
        sampling_mode = self._effective_value(attrs, "sampling_mode")
        sampling_total_count = self._effective_value(attrs, "sampling_total_count")
        sampling_match_scope = self._effective_value(attrs, "sampling_match_scope")
        sampling_kcs = attrs.get("sampling_kcs")
        type_quotas = attrs.get("type_quotas")
        if (
            self.instance is not None
            and self.instance.sampling_mode is None
            and (sampling_mode is None or sampling_kcs is None)
        ):
            raise ApiConflict(
                "旧版草稿只读；请先显式转换为完整的新抽题配置。",
                code="LEGACY_CONVERSION_REQUIRED",
            )
        if self.instance is None and sampling_kcs is None:
            sampling_kcs = []
        if self.instance is None and sampling_mode is None:
            raise serializers.ValidationError({"sampling_mode": "请选择抽题模式。"})
        if sampling_kcs is not None:
            kc_codes = [row["kc_code"] for row in sampling_kcs]
            if len(kc_codes) != len(set(kc_codes)):
                raise serializers.ValidationError(
                    {"sampling_kcs": "同一知识点只能选择一次。"}
                )
            concepts_by_code = {
                concept.code: concept
                for concept in Concept.objects.filter(
                    course=self.context["course"],
                    code__in=kc_codes,
                    status=Concept.Status.ACTIVE,
                )
            }
            if set(concepts_by_code) != set(kc_codes):
                raise serializers.ValidationError(
                    {"sampling_kcs": "包含不存在、已归档或不属于当前课程的知识点。"}
                )
            for row in sampling_kcs:
                row["concept_id"] = concepts_by_code[row.pop("kc_code")].pk
            if sampling_mode == Experiment.SamplingMode.TOTAL_COUNT:
                if sampling_match_scope not in Experiment.MatchScope.values:
                    raise serializers.ValidationError(
                        {"sampling_match_scope": "请选择匹配层级。"}
                    )
                for row in sampling_kcs:
                    row["match_scope"] = sampling_match_scope
            if sampling_mode == Experiment.SamplingMode.TOTAL_COUNT:
                if not sampling_total_count:
                    raise serializers.ValidationError(
                        {"sampling_total_count": "总题数模式必须设置题数。"}
                    )
                if any(row.get("quota") is not None for row in sampling_kcs):
                    raise serializers.ValidationError(
                        {"sampling_kcs": "总题数模式不能设置知识点配额。"}
                    )
            elif sampling_mode == Experiment.SamplingMode.KC_QUOTA:
                if sampling_total_count is not None or not sampling_kcs or any(
                    not row.get("quota") for row in sampling_kcs
                ):
                    raise serializers.ValidationError(
                        {"sampling_kcs": "知识点配额模式必须为每个节点设置正整数配额。"}
                    )
            else:
                raise serializers.ValidationError({"sampling_mode": "请选择抽题模式。"})
        if sampling_mode == Experiment.SamplingMode.KC_QUOTA:
            if type_quotas is not None:
                raise serializers.ValidationError(
                    {"sampling_type_quotas": "知识点配额模式不能携带题型构成。"}
                )
            attrs["type_quotas"] = []
            effective_type_quotas = []
        elif type_quotas is None and self.instance is not None:
            effective_type_quotas = [
                {"question_type": row.question_type, "amount": row.amount}
                for row in self.instance.question_quotas.filter(amount__gt=0)
            ]
        else:
            effective_type_quotas = type_quotas or []
        question_types = [row["question_type"] for row in effective_type_quotas]
        if len(question_types) != len(set(question_types)):
            raise serializers.ValidationError(
                {"sampling_type_quotas": "同一题型只能设置一次。"}
            )
        if sampling_mode != Experiment.SamplingMode.KC_QUOTA and effective_type_quotas and sum(
            row["amount"] for row in effective_type_quotas
        ) != sampling_total_count:
            raise serializers.ValidationError(
                {"sampling_type_quotas": "题型构成数量合计必须严格等于每位参与者题目数。"}
            )
        elif self.instance is None and type_quotas is None:
            attrs["type_quotas"] = []
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

        policy = self._effective_value(attrs, "cross_experiment_item_policy")
        review_count = self._effective_value(attrs, "review_count")
        assignment_size = (
            sampling_total_count
            if sampling_mode == Experiment.SamplingMode.TOTAL_COUNT
            else sum(
                row.get("quota") or 0
                for row in (
                    sampling_kcs
                    if sampling_kcs is not None
                    else self.instance.sampling_kcs.values("quota")
                    if self.instance is not None
                    else []
                )
            )
        )
        if policy == Experiment.CrossExperimentItemPolicy.MIXED_REVIEW:
            if not assignment_size or not 1 <= review_count < assignment_size:
                raise serializers.ValidationError(
                    {"review_count": "混合复测题数必须大于 0 且小于本次总题数。"}
                )
        elif review_count != 0:
            raise serializers.ValidationError(
                {"review_count": "仅混合复测策略可以设置复测题数。"}
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

    def _save_sampling_kcs(self, experiment, sampling_kcs):
        if sampling_kcs is None:
            return
        experiment.sampling_kcs.all().delete()
        ExperimentSamplingKC.objects.bulk_create(
            [
                ExperimentSamplingKC(
                    experiment=experiment,
                    concept_id=row["concept_id"],
                    quota=row.get("quota"),
                    match_scope=row["match_scope"],
                )
                for row in sampling_kcs
            ]
        )

    def create(self, validated_data):
        quotas = validated_data.pop("type_quotas", None)
        validated_data.pop("question_quotas", None)
        sampling_kcs = validated_data.pop("sampling_kcs", None)
        banks = validated_data.pop("selected_banks")
        validated_data["question_bank"] = banks[0]
        experiment = Experiment(**validated_data)
        try:
            experiment.full_clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict) from exc
        experiment.save()
        replace_question_banks(experiment, [bank.pk for bank in banks])
        self._save_quotas(experiment, quotas)
        self._save_sampling_kcs(experiment, sampling_kcs)
        try:
            SamplingService(experiment).resolve(validate_inventory=False)
        except SamplingValidationError as exc:
            raise serializers.ValidationError(
                {issue["field"] or "sampling_kcs": issue["message"] for issue in exc.issues}
            ) from exc
        sync_experiment_questions(experiment)
        return experiment

    def update(self, instance, validated_data):
        quotas = validated_data.pop("type_quotas", None)
        validated_data.pop("question_quotas", None)
        sampling_kcs = validated_data.pop("sampling_kcs", None)
        original_bank_ids = instance.selected_question_bank_ids
        banks = validated_data.pop("selected_banks", None)
        for field_name, value in validated_data.items():
            setattr(instance, field_name, value)
        instance.save()
        if banks is not None:
            replace_question_banks(instance, [bank.pk for bank in banks])
            instance.question_bank = banks[0]
            if hasattr(instance, "_prefetched_objects_cache"):
                instance._prefetched_objects_cache.pop("question_bank_links", None)
        self._save_quotas(instance, quotas)
        self._save_sampling_kcs(instance, sampling_kcs)
        try:
            instance.full_clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict) from exc
        if instance.sampling_mode:
            try:
                SamplingService(instance).resolve(validate_inventory=False)
            except SamplingValidationError as exc:
                raise serializers.ValidationError(
                    {issue["field"] or "sampling_kcs": issue["message"] for issue in exc.issues}
                ) from exc
        if instance.selected_question_bank_ids != original_bank_ids:
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


class ExperimentManagementCapabilitiesSerializer(serializers.Serializer):
    can_manage = serializers.BooleanField()


class TeacherExperimentCollectionSerializer(serializers.Serializer):
    items = TeacherExperimentSerializer(many=True)
    page = PageMetadataSerializer()
    summary = TeacherExperimentSummarySerializer()
    capabilities = ExperimentManagementCapabilitiesSerializer()


class TeacherExperimentReadinessIssueSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()
    class_id = serializers.IntegerField(allow_null=True)
    kc_id = serializers.IntegerField(allow_null=True)
    kc_code = serializers.CharField(allow_null=True, required=False)
    field = serializers.CharField(allow_null=True)
    ancestor_kc_id = serializers.IntegerField(required=False)
    ancestor_kc_path = serializers.CharField(required=False)
    descendant_kc_id = serializers.IntegerField(required=False)
    descendant_kc_path = serializers.CharField(required=False)


class TeacherExperimentInventoryBucketSerializer(serializers.Serializer):
    kc_code = serializers.CharField()
    kc_name = serializers.CharField()
    match_scope = serializers.ChoiceField(choices=Experiment.MatchScope.choices)
    requested = serializers.IntegerField(allow_null=True)
    available = serializers.IntegerField(min_value=0)


class TeacherExperimentReadinessSerializer(serializers.Serializer):
    ready = serializers.BooleanField()
    issues = serializers.ListField(child=serializers.CharField())
    blocking_issues = TeacherExperimentReadinessIssueSerializer(many=True)
    candidate_count = serializers.IntegerField(min_value=0)
    requested_count = serializers.IntegerField(allow_null=True)
    inventory_buckets = TeacherExperimentInventoryBucketSerializer(many=True)
    participant_count = serializers.IntegerField(min_value=0)


class TeacherSamplingInventoryRequestSerializer(serializers.Serializer):
    question_bank_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False
    )
    question_bank_id = serializers.IntegerField(min_value=1, required=False)
    kc_codes = serializers.ListField(
        child=serializers.CharField(max_length=32),
        allow_empty=True,
    )
    match_scope = serializers.ChoiceField(choices=Experiment.MatchScope.choices)

    def validate(self, attrs):
        ids = attrs.get("question_bank_ids")
        if ids is None and attrs.get("question_bank_id"):
            ids = [attrs["question_bank_id"]]
        if not ids:
            raise serializers.ValidationError({"question_bank_ids": "至少选择一个题库。"})
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError({"question_bank_ids": "题库不能重复选择。"})
        attrs["question_bank_ids"] = ids
        return attrs

    def validate_kc_codes(self, codes):
        if len(codes) != len(set(codes)):
            raise serializers.ValidationError("同一知识点只能选择一次。")
        return codes


class TeacherSamplingInventoryQuestionTypeSerializer(serializers.Serializer):
    question_type = serializers.ChoiceField(choices=Question.Type.choices)
    label = serializers.CharField()
    available = serializers.IntegerField(min_value=0)


class TeacherSamplingInventoryKCSerializer(serializers.Serializer):
    kc_code = serializers.CharField()
    primary_available = serializers.IntegerField(min_value=0)
    any_level_available = serializers.IntegerField(min_value=0)


class TeacherSamplingInventoryResponseSerializer(serializers.Serializer):
    scope = serializers.CharField()
    candidate_count = serializers.IntegerField(min_value=0)
    question_types = TeacherSamplingInventoryQuestionTypeSerializer(many=True)
    kc_counts = TeacherSamplingInventoryKCSerializer(many=True)
    bank_counts = serializers.ListField(child=serializers.DictField(), required=False)


class PublishPlanClassSerializer(serializers.Serializer):
    class_id = serializers.IntegerField()
    class_name = serializers.CharField()
    academic_year = serializers.CharField()
    excluded_count = serializers.IntegerField(min_value=0)
    participant_count = serializers.IntegerField(min_value=0)
    schedule_mode = serializers.ChoiceField(choices=("default", "custom"))
    opens_at = serializers.DateTimeField(allow_null=True)
    closes_at = serializers.DateTimeField(allow_null=True)


class PublishPlanBlockingIssueSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()
    scope = serializers.CharField(required=False)
    class_id = serializers.IntegerField(allow_null=True)
    kc_id = serializers.IntegerField(allow_null=True)
    field = serializers.CharField(allow_null=True)
    question_type = serializers.CharField(required=False)
    requested = serializers.IntegerField(required=False)
    available = serializers.IntegerField(required=False)
    ancestor_kc_id = serializers.IntegerField(required=False)
    ancestor_kc_path = serializers.CharField(required=False)
    descendant_kc_id = serializers.IntegerField(required=False)
    descendant_kc_path = serializers.CharField(required=False)
    student_id = serializers.IntegerField(required=False)
    student_number = serializers.CharField(required=False, allow_blank=True)


class PublishPlanGlobalQuestionTypeSerializer(serializers.Serializer):
    question_type = serializers.CharField()
    requested = serializers.IntegerField(min_value=0)
    available = serializers.IntegerField(min_value=0)


class PublishPlanStudentQuestionTypeSerializer(serializers.Serializer):
    question_type = serializers.CharField()
    requested = serializers.IntegerField(min_value=0)
    minimum_available = serializers.IntegerField(min_value=0)
    insufficient_student_count = serializers.IntegerField(min_value=0)


class PublishPlanGlobalInventorySerializer(serializers.Serializer):
    scope = serializers.CharField()
    candidate_count = serializers.IntegerField(min_value=0)
    requested_count = serializers.IntegerField(min_value=0)
    question_types = PublishPlanGlobalQuestionTypeSerializer(many=True)


class PublishPlanStudentInventorySerializer(serializers.Serializer):
    scope = serializers.CharField()
    checked = serializers.BooleanField()
    participant_count = serializers.IntegerField(min_value=0)
    minimum_candidate_count = serializers.IntegerField(min_value=0)
    question_types = PublishPlanStudentQuestionTypeSerializer(many=True)
    blocking_issue_count = serializers.IntegerField(min_value=0)


class PublishPlanSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    experiment_id = serializers.IntegerField()
    status = serializers.CharField()
    current_step = serializers.IntegerField(min_value=1, max_value=2)
    setup_status = serializers.ChoiceField(choices=("empty", "incomplete", "complete"))
    missing_requirements = serializers.ListField(child=serializers.CharField())
    blocking_issues = PublishPlanBlockingIssueSerializer(many=True)
    can_preview = serializers.BooleanField()
    global_inventory = PublishPlanGlobalInventorySerializer()
    student_inventory = PublishPlanStudentInventorySerializer()
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


class PublishOperationJobSerializer(serializers.Serializer):
    job_id = serializers.UUIDField()
    operation = serializers.ChoiceField(choices=("preview", "commit"))
    status = serializers.ChoiceField(
        choices=("queued", "running", "succeeded", "failed", "stale")
    )
    status_url = serializers.CharField()
    plan_revision = serializers.IntegerField(min_value=1)
    stage = serializers.CharField()
    processed = serializers.IntegerField(min_value=0)
    total = serializers.IntegerField(min_value=0)
    error_code = serializers.CharField(allow_blank=True)
    error_message = serializers.CharField(allow_blank=True)
    preview_run_id = serializers.IntegerField(allow_null=True)
    release_snapshot_id = serializers.IntegerField(allow_null=True)


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
    review_item_count = serializers.IntegerField(min_value=0)
    new_item_count = serializers.IntegerField(min_value=0)
    cross_experiment_item_policy = serializers.ChoiceField(
        choices=Experiment.CrossExperimentItemPolicy.choices,
        allow_null=True,
    )


class PublishParticipantCollectionSerializer(serializers.Serializer):
    items = PublishParticipantSerializer(many=True)
    page = PageMetadataSerializer()


class CollectionParticipantQuerySerializer(serializers.Serializer):
    class_id = serializers.IntegerField(
        min_value=1, allow_null=True, required=False, default=None
    )
    status = serializers.ChoiceField(
        choices=COLLECTION_STATUS_CHOICES,
        allow_blank=True,
        required=False,
        default="",
    )
    q = serializers.CharField(
        allow_blank=True,
        required=False,
        default="",
        trim_whitespace=True,
    )
    page = serializers.IntegerField(min_value=1, required=False, default=1)


class CollectionParticipantChoiceSerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()


class CollectionParticipantClassChoiceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class CollectionParticipantRowSerializer(serializers.Serializer):
    participant_id = serializers.IntegerField()
    student_id = serializers.IntegerField()
    student_number = serializers.CharField()
    display_name = serializers.CharField()
    class_id = serializers.IntegerField()
    class_name = serializers.CharField()
    status = serializers.ChoiceField(choices=COLLECTION_STATUS_CHOICES)
    status_label = serializers.CharField()
    started_at = serializers.DateTimeField(allow_null=True)
    ended_at = serializers.DateTimeField(allow_null=True)
    submission_count = serializers.IntegerField(min_value=0)


class CollectionParticipantFilterSerializer(serializers.Serializer):
    class_id = serializers.IntegerField(allow_null=True)
    status = serializers.ChoiceField(
        choices=COLLECTION_STATUS_CHOICES, allow_blank=True
    )
    q = serializers.CharField(allow_blank=True)


class CollectionParticipantCollectionSerializer(serializers.Serializer):
    items = CollectionParticipantRowSerializer(many=True)
    page = PageMetadataSerializer()
    filters = CollectionParticipantFilterSerializer()
    class_choices = CollectionParticipantClassChoiceSerializer(many=True)
    status_choices = CollectionParticipantChoiceSerializer(many=True)


class PublishParticipantItemSerializer(serializers.Serializer):
    assigned_sequence_index = serializers.IntegerField(min_value=1)
    question_id = serializers.IntegerField()
    question_code = serializers.CharField()
    question_type = serializers.CharField()
    history_role = serializers.ChoiceField(
        choices=("review", "new", "unrestricted"),
        allow_null=True,
    )


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
    total_time_limit_minutes = serializers.IntegerField(allow_null=True)
    per_question_time_limit_seconds = serializers.IntegerField(allow_null=True)
    recoverable_count = serializers.IntegerField(min_value=0)
    participant_count = serializers.IntegerField(min_value=0)
    grading = serializers.DictField()
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
    opens_at = serializers.DateTimeField(allow_null=True, required=False)
    closes_at = serializers.DateTimeField(allow_null=True, required=False)
    expected_revision = serializers.IntegerField(min_value=1, required=False)
    extend_minutes = serializers.IntegerField(min_value=1, max_value=10080, required=False)
    reopen = serializers.BooleanField(required=False, default=False)
    total_time_limit_minutes = serializers.IntegerField(min_value=1, required=False)
    per_question_time_limit_seconds = serializers.IntegerField(min_value=1, required=False)
    reason = serializers.CharField(max_length=500, allow_blank=True, required=False)

    def validate(self, attrs):
        if (
            attrs.get("opens_at") is not None
            and attrs.get("closes_at") is not None
            and attrs["closes_at"] <= attrs["opens_at"]
        ):
            raise serializers.ValidationError("截止时间必须晚于开始时间。")
        return attrs


class TaskReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500, allow_blank=True, required=False)
