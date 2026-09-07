from rest_framework import serializers

from .serializers import CourseReferenceSerializer


class TeachingLegacyScopeSerializer(serializers.Serializer):
    values = serializers.DictField(child=serializers.IntegerField())
    experiment_id = serializers.IntegerField(allow_null=True)
    class_id = serializers.IntegerField(allow_null=True)
    is_complete = serializers.BooleanField()
    display = serializers.CharField()


class TeachingLegacyFiltersSerializer(serializers.Serializer):
    q = serializers.CharField(allow_blank=True)
    student_id = serializers.IntegerField(allow_null=True)
    session_id = serializers.IntegerField(allow_null=True)


class TeachingLegacyStudentSummarySerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    student_number = serializers.CharField()
    display_name = serializers.CharField()
    data_status = serializers.CharField()
    data_status_label = serializers.CharField()
    attempt_count = serializers.IntegerField(allow_null=True)
    submitted_count = serializers.IntegerField(allow_null=True)
    graded_count = serializers.IntegerField(allow_null=True)
    correct_rate = serializers.IntegerField(allow_null=True)
    score = serializers.DecimalField(
        max_digits=20,
        decimal_places=2,
        allow_null=True,
    )
    max_score = serializers.DecimalField(
        max_digits=20,
        decimal_places=2,
        allow_null=True,
    )


class TeachingLegacySessionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    status = serializers.CharField()
    status_label = serializers.CharField()
    attempt_count = serializers.IntegerField(min_value=0)
    submitted_count = serializers.IntegerField(min_value=0)
    started_at = serializers.DateTimeField()


class TeachingLegacyTimelineEventSerializer(serializers.Serializer):
    event_id = serializers.CharField()
    event_type = serializers.CharField()
    occurred_at = serializers.DateTimeField()
    interval_quality = serializers.CharField()


class TeachingLegacyTimingSerializer(serializers.Serializer):
    display_duration_ms = serializers.IntegerField(allow_null=True)
    display_label = serializers.CharField()


class TeachingLegacyAttemptSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    question_code = serializers.CharField()
    status = serializers.CharField()
    status_label = serializers.CharField()
    grade_score = serializers.DecimalField(
        max_digits=20,
        decimal_places=2,
        allow_null=True,
    )
    timing = TeachingLegacyTimingSerializer()
    timeline = TeachingLegacyTimelineEventSerializer(many=True)
    raw_events_url = serializers.CharField()


class TeachingLegacyResponseSerializer(serializers.Serializer):
    course = CourseReferenceSerializer()
    scope = TeachingLegacyScopeSerializer()
    blocked_message = serializers.CharField(allow_blank=True)
    filters = TeachingLegacyFiltersSerializer()
    total_roster_count = serializers.IntegerField(min_value=0)
    student_summaries = TeachingLegacyStudentSummarySerializer(many=True)
    sessions = TeachingLegacySessionSerializer(many=True)
    attempts = TeachingLegacyAttemptSerializer(many=True)
