from rest_framework import serializers


class FileImportRequestSerializer(serializers.Serializer):
    file = serializers.FileField()


class ImportPreviewResponseSerializer(serializers.Serializer):
    batch_id = serializers.UUIDField()
    preview = serializers.JSONField()


class ImportConfirmRequestSerializer(serializers.Serializer):
    batch_id = serializers.UUIDField()


class CourseKCImportResultSerializer(serializers.Serializer):
    created = serializers.IntegerField(min_value=0)
    unchanged = serializers.IntegerField(min_value=0)


class QuestionKCImportResultSerializer(serializers.Serializer):
    questions_updated = serializers.IntegerField(min_value=0)


class QuestionBankCreateRequestSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=160)
    description = serializers.CharField(required=False, allow_blank=True)
    visibility = serializers.ChoiceField(
        choices=("private", "shared"),
        required=False,
    )


class QuestionBankCreateResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    detail_url = serializers.CharField()


class CourseConceptCreateRequestSerializer(serializers.Serializer):
    sequence = serializers.IntegerField(min_value=1)
    name = serializers.CharField(max_length=160)
    description = serializers.CharField(required=False, allow_blank=True)


class CourseConceptCreateResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kc_code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    status = serializers.CharField()


class QuestionBankCourseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    code = serializers.CharField()
    term = serializers.CharField()
    status = serializers.CharField()


class QuestionBankPageMetadataSerializer(serializers.Serializer):
    number = serializers.IntegerField(min_value=1)
    size = serializers.IntegerField(min_value=1)
    total_items = serializers.IntegerField(min_value=0)
    total_pages = serializers.IntegerField(min_value=1)
    start = serializers.IntegerField(min_value=0)
    end = serializers.IntegerField(min_value=0)


class QuestionBankSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    description = serializers.CharField()
    visibility = serializers.CharField()
    visibility_label = serializers.CharField()
    question_count = serializers.IntegerField(min_value=0)
    created_by_name = serializers.CharField()
    updated_at = serializers.DateTimeField()
    detail_url = serializers.CharField()
    visibility_update_url = serializers.CharField(allow_null=True)
    archive_url = serializers.CharField(allow_null=True)
    can_edit_metadata = serializers.BooleanField()


class QuestionBankCollectionSummarySerializer(serializers.Serializer):
    archived_count = serializers.IntegerField(min_value=0)
    archived_banks_url = serializers.CharField()
    shared_sources_url = serializers.CharField()


class QuestionBankCollectionSerializer(serializers.Serializer):
    items = QuestionBankSummarySerializer(many=True)
    page = QuestionBankPageMetadataSerializer()
    course = QuestionBankCourseSerializer()
    summary = QuestionBankCollectionSummarySerializer()


class QuestionBankDistributionSerializer(serializers.Serializer):
    bank_id = serializers.IntegerField()
    bank_name = serializers.CharField()
    question_count = serializers.IntegerField(min_value=0)
    detail_url = serializers.CharField()


class KCRoleCountsSerializer(serializers.Serializer):
    level_1 = serializers.IntegerField(min_value=0)
    level_2 = serializers.IntegerField(min_value=0)
    level_3 = serializers.IntegerField(min_value=0)


class CourseConceptSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kc_code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    status = serializers.CharField()
    role_counts = KCRoleCountsSerializer()
    question_count = serializers.IntegerField(min_value=0)
    bank_count = serializers.IntegerField(min_value=0)
    distribution = QuestionBankDistributionSerializer(many=True)
    can_rename = serializers.BooleanField()
    can_delete = serializers.BooleanField()
    rename_url = serializers.CharField(allow_null=True)
    delete_url = serializers.CharField(allow_null=True)


class CourseConceptCollectionSerializer(serializers.Serializer):
    items = CourseConceptSummarySerializer(many=True)
    page = QuestionBankPageMetadataSerializer()
    course = QuestionBankCourseSerializer()
    summary = serializers.DictField()


class QuestionBankDetailSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    description = serializers.CharField()
    visibility = serializers.CharField()
    visibility_label = serializers.CharField()
    question_count = serializers.IntegerField(min_value=0)
    created_by_name = serializers.CharField()
    updated_at = serializers.DateTimeField()
    can_edit_metadata = serializers.BooleanField()
    can_edit_content = serializers.BooleanField()
    list_url = serializers.CharField()
    edit_url = serializers.CharField(allow_null=True)
    visibility_update_url = serializers.CharField(allow_null=True)
    archive_url = serializers.CharField(allow_null=True)
    restore_url = serializers.CharField(allow_null=True)
    delete_url = serializers.CharField(allow_null=True)
    question_import_url = serializers.CharField(allow_null=True)
    kc_import_url = serializers.CharField(allow_null=True)
    course = QuestionBankCourseSerializer()


class TeacherQuestionBankReferenceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class QuestionBankConceptReferenceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    path = serializers.CharField()
    depth = serializers.IntegerField(min_value=1)


class QuestionKCMappingNodeSerializer(serializers.Serializer):
    kc_code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    status = serializers.CharField()
    depth = serializers.IntegerField(min_value=1, max_value=3)
    parent_kc_code = serializers.CharField(allow_null=True)
    order = serializers.IntegerField(min_value=1)


class QuestionKCMappingSerializer(serializers.Serializer):
    schema_version = serializers.CharField()
    root_kc_code = serializers.CharField(allow_null=True)
    nodes = QuestionKCMappingNodeSerializer(many=True)
    mapping_hash = serializers.CharField()


class QuestionSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    course_sequence = serializers.IntegerField(allow_null=True, min_value=1)
    stable_code = serializers.CharField()
    sequence = serializers.IntegerField(min_value=1)
    code = serializers.CharField()
    text = serializers.CharField()
    question_type = serializers.CharField()
    question_type_label = serializers.CharField()
    kc_mapping = QuestionKCMappingSerializer()
    teacher_difficulty = serializers.DecimalField(
        max_digits=4,
        decimal_places=3,
        allow_null=True,
    )
    status = serializers.CharField()
    status_label = serializers.CharField()
    edit_url = serializers.CharField(allow_null=True)
    delete_url = serializers.CharField(allow_null=True)


class FilterChoiceSerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()


class QuestionCollectionFiltersSerializer(serializers.Serializer):
    query = serializers.CharField()
    question_type = serializers.CharField()
    status = serializers.CharField()
    question_types = FilterChoiceSerializer(many=True)
    statuses = FilterChoiceSerializer(many=True)


class QuestionCollectionSerializer(serializers.Serializer):
    items = QuestionSummarySerializer(many=True)
    page = QuestionBankPageMetadataSerializer()
    bank = TeacherQuestionBankReferenceSerializer()
    filters = QuestionCollectionFiltersSerializer()


class BankConceptQuestionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    sequence = serializers.IntegerField(min_value=1)
    code = serializers.CharField()
    edit_url = serializers.CharField(allow_null=True)


class BankConceptSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kc_code = serializers.CharField()
    name = serializers.CharField()
    level_1_count = serializers.IntegerField(min_value=0)
    level_2_count = serializers.IntegerField(min_value=0)
    level_3_count = serializers.IntegerField(min_value=0)
    question_count = serializers.IntegerField(min_value=0)
    questions = BankConceptQuestionSerializer(many=True)


class BankConceptCollectionSerializer(serializers.Serializer):
    items = BankConceptSummarySerializer(many=True)
    page = QuestionBankPageMetadataSerializer()
    bank = TeacherQuestionBankReferenceSerializer()
