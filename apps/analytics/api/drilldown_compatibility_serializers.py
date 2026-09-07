from rest_framework import serializers

from .serializers import CourseReferenceSerializer


class TeacherExperimentReferenceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    status = serializers.CharField()


class TeacherExperimentTaskCompatibilitySerializer(serializers.Serializer):
    academic_year = serializers.CharField(allow_blank=True)
    class_name = serializers.CharField()
    class_snapshot_name = serializers.CharField()
    analysis_url = serializers.CharField()


class TeacherExperimentSessionCompatibilitySerializer(serializers.Serializer):
    student_number = serializers.CharField()
    display_name = serializers.CharField()
    status = serializers.CharField()
    status_label = serializers.CharField()
    started_at = serializers.DateTimeField()
    submission_count = serializers.IntegerField(min_value=0)
    detail_url = serializers.CharField()


class TeacherSessionStatusChoiceSerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()


class TeacherExperimentResultsActionsSerializer(serializers.Serializer):
    back_url = serializers.CharField()
    clear_filter_url = serializers.CharField()


class TeacherExperimentResultsResolutionSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=("canonical", "class_tasks", "sessions"))
    canonical_url = serializers.CharField(allow_null=True)
    course = CourseReferenceSerializer()
    experiment = TeacherExperimentReferenceSerializer()
    tasks = TeacherExperimentTaskCompatibilitySerializer(many=True)
    sessions = TeacherExperimentSessionCompatibilitySerializer(many=True)
    session_status_choices = TeacherSessionStatusChoiceSerializer(many=True)
    selected_status = serializers.CharField(allow_blank=True)
    actions = TeacherExperimentResultsActionsSerializer()


class TeacherSessionReportOptionSerializer(serializers.Serializer):
    key = serializers.CharField(allow_blank=True)
    text = serializers.CharField()
    display = serializers.CharField()


class TeacherSessionReportQuestionSerializer(serializers.Serializer):
    assigned_sequence_index = serializers.IntegerField(min_value=0)
    stable_code = serializers.CharField()
    question_type = serializers.CharField()
    question_type_label = serializers.CharField()
    text = serializers.CharField()
    options = TeacherSessionReportOptionSerializer(many=True)
    student_answer = serializers.CharField()
    correct_answer = serializers.CharField()
    answer_status = serializers.CharField()


class TeacherSessionReportSerializer(serializers.Serializer):
    student_name = serializers.CharField()
    student_number = serializers.CharField()
    class_snapshot_name = serializers.CharField(allow_blank=True)
    version_number = serializers.IntegerField(min_value=0)
    session_status = serializers.CharField()
    question_count = serializers.IntegerField(min_value=0)
    questions = TeacherSessionReportQuestionSerializer(many=True)


class TeacherExperimentSessionActionsSerializer(serializers.Serializer):
    back_url = serializers.CharField()
    word_url = serializers.CharField(allow_null=True)


class TeacherExperimentSessionResolutionSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=("canonical", "legacy_report"))
    canonical_url = serializers.CharField(allow_null=True)
    course = CourseReferenceSerializer()
    experiment = TeacherExperimentReferenceSerializer()
    report = TeacherSessionReportSerializer(allow_null=True)
    actions = TeacherExperimentSessionActionsSerializer()
