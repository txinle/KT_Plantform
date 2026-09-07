from rest_framework import serializers

from .serializers import (
    QuestionBankCourseSerializer,
    QuestionBankPageMetadataSerializer,
    QuestionKCMappingSerializer,
)


QUESTION_TYPE_CHOICES = (
    "single_choice",
    "multiple_choice",
    "numeric",
    "short_answer",
    "fill_blank",
    "true_false",
)
QUESTION_STATUS_CHOICES = ("draft", "active", "archived")
BANK_VISIBILITY_CHOICES = ("private", "shared", "archived")


class ManagementErrorDetailSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()
    fields = serializers.DictField(required=False)


class ManagementErrorResponseSerializer(serializers.Serializer):
    error = ManagementErrorDetailSerializer()


class ManagementChoiceSerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()


class ManagementBankReferenceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class ManagementDeleteConfirmationSerializer(serializers.Serializer):
    confirmation = serializers.ChoiceField(choices=("delete",))


class ManagementBankVisibilityResultSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    visibility = serializers.ChoiceField(choices=BANK_VISIBILITY_CHOICES)


class ManagementArchivedBankSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    description = serializers.CharField()
    question_count = serializers.IntegerField(min_value=0)
    created_by_name = serializers.CharField()
    updated_at = serializers.DateTimeField()
    detail_url = serializers.CharField()
    can_edit_metadata = serializers.BooleanField()


class ManagementArchivedBankCollectionSerializer(serializers.Serializer):
    course = QuestionBankCourseSerializer()
    page = QuestionBankPageMetadataSerializer()
    items = ManagementArchivedBankSerializer(many=True)


class ManagementBankEditorRequestSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=160)
    description = serializers.CharField(allow_blank=True)
    visibility = serializers.ChoiceField(choices=BANK_VISIBILITY_CHOICES)


class ManagementBankEditorResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    description = serializers.CharField()
    visibility = serializers.ChoiceField(choices=BANK_VISIBILITY_CHOICES)
    visibility_choices = ManagementChoiceSerializer(many=True)
    course = QuestionBankCourseSerializer()
    detail_url = serializers.CharField()


class ManagementSharedSourceCourseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    code = serializers.CharField()
    term = serializers.CharField()
    shared_bank_count = serializers.IntegerField(min_value=0)
    shared_creator_count = serializers.IntegerField(min_value=0)


class ManagementSharedSourceCourseCollectionSerializer(serializers.Serializer):
    course = QuestionBankCourseSerializer()
    items = ManagementSharedSourceCourseSerializer(many=True)


class ManagementSharedCreatorSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    username = serializers.CharField()
    shared_bank_count = serializers.IntegerField(min_value=0)


class ManagementSharedCreatorCollectionSerializer(serializers.Serializer):
    course = QuestionBankCourseSerializer()
    source_course = QuestionBankCourseSerializer()
    items = ManagementSharedCreatorSerializer(many=True)


class ManagementSharedBankSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    description = serializers.CharField()
    question_count = serializers.IntegerField(min_value=0)


class ManagementSharedQuestionBankCollectionSerializer(serializers.Serializer):
    course = QuestionBankCourseSerializer()
    source_course = QuestionBankCourseSerializer()
    creator = ManagementSharedCreatorSerializer()
    items = ManagementSharedBankSerializer(many=True)


class ManagementSharedQuestionBankCopySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    detail_url = serializers.CharField()


class ManagementQuestionEditorOptionsSerializer(serializers.Serializer):
    a = serializers.CharField(allow_blank=True)
    b = serializers.CharField(allow_blank=True)
    c = serializers.CharField(allow_blank=True)
    d = serializers.CharField(allow_blank=True)
    e = serializers.CharField(allow_blank=True)
    f = serializers.CharField(allow_blank=True)


class ManagementQuestionEditorQuestionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    sequence = serializers.IntegerField(min_value=1)
    code = serializers.CharField()
    text = serializers.CharField()
    question_type = serializers.ChoiceField(choices=QUESTION_TYPE_CHOICES)
    status = serializers.ChoiceField(choices=QUESTION_STATUS_CHOICES)
    teacher_difficulty = serializers.CharField(allow_blank=True)
    absolute_tolerance = serializers.CharField(allow_blank=True)
    relative_tolerance = serializers.CharField(allow_blank=True)
    explanation = serializers.CharField(allow_blank=True)
    correct_answer_text = serializers.CharField(allow_blank=True)
    accepted_answers_text = serializers.CharField(allow_blank=True)
    options = ManagementQuestionEditorOptionsSerializer()
    kc_mapping = QuestionKCMappingSerializer(required=False)


class ManagementQuestionEditorConceptSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kc_code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()


class ManagementQuestionEditorResponseSerializer(serializers.Serializer):
    course = QuestionBankCourseSerializer(required=False)
    bank = ManagementBankReferenceSerializer()
    question = ManagementQuestionEditorQuestionSerializer()
    question_types = ManagementChoiceSerializer(many=True)
    statuses = ManagementChoiceSerializer(many=True)
    concepts = ManagementQuestionEditorConceptSerializer(many=True)
    detail_url = serializers.CharField()


class ManagementQuestionEditorRequestSerializer(serializers.Serializer):
    text = serializers.CharField()
    question_type = serializers.ChoiceField(choices=QUESTION_TYPE_CHOICES)
    status = serializers.ChoiceField(choices=QUESTION_STATUS_CHOICES)
    teacher_difficulty = serializers.CharField(allow_blank=True, required=False)
    absolute_tolerance = serializers.CharField(allow_blank=True, required=False)
    relative_tolerance = serializers.CharField(allow_blank=True, required=False)
    explanation = serializers.CharField(allow_blank=True, required=False)
    correct_answer_text = serializers.CharField(allow_blank=True, required=False)
    accepted_answers_text = serializers.CharField(allow_blank=True, required=False)
    option_a = serializers.CharField(allow_blank=True, required=False)
    option_b = serializers.CharField(allow_blank=True, required=False)
    option_c = serializers.CharField(allow_blank=True, required=False)
    option_d = serializers.CharField(allow_blank=True, required=False)
    option_e = serializers.CharField(allow_blank=True, required=False)
    option_f = serializers.CharField(allow_blank=True, required=False)
    kc_mapping_json = serializers.CharField(allow_blank=True, required=False)


class ManagementQuestionImportPreviewRowSerializer(serializers.Serializer):
    sequence = serializers.IntegerField(min_value=1)
    text = serializers.CharField()
    question_type = serializers.ChoiceField(choices=QUESTION_TYPE_CHOICES)
    question_type_label = serializers.CharField()
    option_count = serializers.IntegerField(min_value=0)
    teacher_difficulty = serializers.CharField(allow_null=True)


class ManagementQuestionImportPreviewPayloadSerializer(serializers.Serializer):
    rows = ManagementQuestionImportPreviewRowSerializer(many=True)


class ManagementQuestionImportPreviewResponseSerializer(serializers.Serializer):
    preview_id = serializers.UUIDField()
    preview = ManagementQuestionImportPreviewPayloadSerializer()


class ManagementQuestionImportConfirmRequestSerializer(serializers.Serializer):
    preview_id = serializers.UUIDField()


class ManagementQuestionImportConfirmResponseSerializer(serializers.Serializer):
    created = serializers.IntegerField(min_value=0)
    detail_url = serializers.CharField()


class ManagementKCPreviewTreeNodeSerializer(serializers.Serializer):
    kc_code = serializers.CharField()
    parent_kc_code = serializers.CharField(allow_null=True)
    order = serializers.IntegerField(min_value=1)


class ManagementKCPreviewMatchTraceSerializer(serializers.Serializer):
    raw_token = serializers.CharField()
    normalized_token = serializers.CharField()
    method = serializers.CharField()
    kc_code = serializers.CharField()
    final_name = serializers.CharField()
    distance = serializers.FloatField(allow_null=True)


class ManagementKCPreviewTreeSerializer(serializers.Serializer):
    sequence = serializers.IntegerField(min_value=1)
    question_id = serializers.IntegerField()
    root_kc_code = serializers.CharField()
    nodes = ManagementKCPreviewTreeNodeSerializer(many=True)
    match_trace = ManagementKCPreviewMatchTraceSerializer(many=True)


class ManagementKCPreviewLegacyRowSerializer(serializers.Serializer):
    sequence = serializers.IntegerField(min_value=1)
    primary = serializers.CharField()
    secondary = serializers.ListField(child=serializers.CharField())


class ManagementKCPreviewWorkbookNodeSerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    status = serializers.CharField()


class ManagementKCPreviewWorkbookQuestionSerializer(serializers.Serializer):
    stable_question_code = serializers.CharField()
    root_kc_code = serializers.CharField()


class ManagementKCImportPreviewPayloadSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=("tree_v2", "workbook", "legacy"))
    confirmable = serializers.BooleanField()
    schema_version = serializers.CharField(required=False)
    trees = ManagementKCPreviewTreeSerializer(many=True, required=False)
    rows = ManagementKCPreviewLegacyRowSerializer(many=True, required=False)
    nodes = ManagementKCPreviewWorkbookNodeSerializer(many=True, required=False)
    questions = ManagementKCPreviewWorkbookQuestionSerializer(many=True, required=False)


class ManagementKCImportPreviewResponseSerializer(serializers.Serializer):
    batch_id = serializers.UUIDField()
    preview = ManagementKCImportPreviewPayloadSerializer()


class ManagementKCImportConfirmRequestSerializer(serializers.Serializer):
    batch_id = serializers.UUIDField()


class ManagementQuestionsUpdatedResponseSerializer(serializers.Serializer):
    questions_updated = serializers.IntegerField(min_value=0)


class ManagementCourseConceptEditRequestSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=160)
    description = serializers.CharField(allow_blank=True)


class ManagementCourseConceptEditResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kc_code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    status = serializers.CharField()


class ManagementCourseConceptDeleteResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    status = serializers.CharField()


class ManagementStandaloneBankSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    description = serializers.CharField()
    visibility = serializers.ChoiceField(choices=BANK_VISIBILITY_CHOICES)
    visibility_label = serializers.CharField()
    question_count = serializers.IntegerField(min_value=0)
    detail_url = serializers.CharField()
    question_import_url = serializers.CharField()
    kc_import_url = serializers.CharField()


class ManagementStandaloneBankCreateRequestSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=160)
    description = serializers.CharField(allow_blank=True, required=False)
    visibility = serializers.ChoiceField(choices=BANK_VISIBILITY_CHOICES, required=False)


class ManagementStandaloneSharedBankSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    description = serializers.CharField()
    question_count = serializers.IntegerField(min_value=0)
    created_by_name = serializers.CharField()


class ManagementStandaloneBankCollectionSerializer(serializers.Serializer):
    redirect_url = serializers.CharField(required=False)
    banks = ManagementStandaloneBankSerializer(many=True, required=False)
    shared_banks = ManagementStandaloneSharedBankSerializer(many=True, required=False)
    visibility_choices = ManagementChoiceSerializer(many=True, required=False)


class ManagementStandaloneQuestionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    sequence = serializers.IntegerField(min_value=1)
    code = serializers.CharField()
    text = serializers.CharField()
    question_type = serializers.ChoiceField(choices=QUESTION_TYPE_CHOICES)
    question_type_label = serializers.CharField()
    status = serializers.ChoiceField(choices=QUESTION_STATUS_CHOICES)
    status_label = serializers.CharField()
    teacher_difficulty = serializers.CharField(allow_null=True)
    kc_mapping = QuestionKCMappingSerializer()
    edit_url = serializers.CharField()


class ManagementStandaloneBankDetailSerializer(serializers.Serializer):
    redirect_url = serializers.CharField(required=False)
    bank = ManagementStandaloneBankSerializer(required=False)
    can_edit_metadata = serializers.BooleanField(required=False)
    can_edit_content = serializers.BooleanField(required=False)
    questions = ManagementStandaloneQuestionSerializer(many=True, required=False)


class ManagementStandaloneVisibilityRequestSerializer(serializers.Serializer):
    visibility = serializers.ChoiceField(choices=BANK_VISIBILITY_CHOICES)


class ManagementStandaloneQuestionEditorResponseSerializer(serializers.Serializer):
    bank = ManagementBankReferenceSerializer()
    question = ManagementQuestionEditorQuestionSerializer()
    question_types = ManagementChoiceSerializer(many=True)
    statuses = ManagementChoiceSerializer(many=True)
    concepts = serializers.ListField(child=serializers.IntegerField(), allow_empty=True)
    detail_url = serializers.CharField()


class ManagementStandaloneKCImportPreviewPayloadSerializer(serializers.Serializer):
    rows = ManagementKCPreviewLegacyRowSerializer(many=True)


class ManagementStandaloneKCImportPreviewResponseSerializer(serializers.Serializer):
    preview_id = serializers.UUIDField()
    preview = ManagementStandaloneKCImportPreviewPayloadSerializer()


class ManagementStandaloneConceptSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    scope = serializers.CharField()
    scope_label = serializers.CharField()
    status = serializers.CharField()
    status_label = serializers.CharField()
    path = serializers.CharField()


class ManagementStandaloneConceptCollectionSerializer(serializers.Serializer):
    redirect_url = serializers.CharField(required=False)
    items = ManagementStandaloneConceptSerializer(many=True, required=False)
