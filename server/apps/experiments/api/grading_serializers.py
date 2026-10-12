from rest_framework import serializers


class ManualGradingTotalsSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    short_answer_questions = serializers.IntegerField(min_value=0)
    submitted = serializers.IntegerField(min_value=0)
    pending = serializers.IntegerField(min_value=0)
    graded = serializers.IntegerField(min_value=0)


class ManualGradingTaskSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    experiment_id = serializers.IntegerField()
    experiment_name = serializers.CharField()
    class_id = serializers.IntegerField()
    class_name = serializers.CharField()


class ManualGradingQuestionSummarySerializer(serializers.Serializer):
    question_snapshot_id = serializers.IntegerField()
    code = serializers.CharField()
    text_summary = serializers.CharField()
    submitted_count = serializers.IntegerField(min_value=0)
    pending_count = serializers.IntegerField(min_value=0)
    graded_count = serializers.IntegerField(min_value=0)


class ManualGradingOverviewSerializer(serializers.Serializer):
    task = ManualGradingTaskSerializer()
    totals = ManualGradingTotalsSerializer()
    questions = ManualGradingQuestionSummarySerializer(many=True)


class ManualGradingSubmissionSerializer(serializers.Serializer):
    submission_id = serializers.IntegerField()
    student_id = serializers.IntegerField()
    student_number = serializers.CharField()
    display_name = serializers.CharField()
    final_answer = serializers.JSONField(allow_null=True)
    submitted_at = serializers.DateTimeField()
    grading_status = serializers.CharField()
    latest_grade_version = serializers.IntegerField(min_value=0)
    current_is_correct = serializers.BooleanField(allow_null=True)
    current_comment = serializers.CharField(allow_blank=True)
    graded_at = serializers.DateTimeField(allow_null=True)
    grader_name = serializers.CharField(allow_blank=True)


class ManualGradingQuestionSerializer(serializers.Serializer):
    question_snapshot_id = serializers.IntegerField()
    code = serializers.CharField()
    text = serializers.CharField()
    correct_answer = serializers.JSONField()
    accepted_answers = serializers.JSONField()
    explanation = serializers.CharField(allow_blank=True)


class ManualGradingPageSerializer(serializers.Serializer):
    number = serializers.IntegerField(min_value=1)
    size = serializers.IntegerField(min_value=1)
    total_items = serializers.IntegerField(min_value=0)
    total_pages = serializers.IntegerField(min_value=0)
    start = serializers.IntegerField(min_value=0)
    end = serializers.IntegerField(min_value=0)


class ManualGradingSubmissionCollectionSerializer(serializers.Serializer):
    items = ManualGradingSubmissionSerializer(many=True)
    page = ManualGradingPageSerializer()


class ManualGradingQuestionDetailSerializer(serializers.Serializer):
    question = ManualGradingQuestionSerializer()
    submissions = ManualGradingSubmissionCollectionSerializer()


class ManualGradeInputSerializer(serializers.Serializer):
    expected_grade_version = serializers.IntegerField(min_value=1)
    is_correct = serializers.BooleanField()
    comment = serializers.CharField(max_length=2000, allow_blank=True, required=False)


class ManualGradeResponseSerializer(serializers.Serializer):
    submission = ManualGradingSubmissionSerializer()
    question_progress = ManualGradingQuestionSummarySerializer()
    task_progress = ManualGradingTotalsSerializer()
