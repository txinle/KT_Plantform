from rest_framework import serializers

from apps.accounts.models import Course


class CourseContextSerializer(serializers.ModelSerializer):
    class Meta:
        model = Course
        fields = ("id", "name", "code", "term", "status")
        read_only_fields = fields
