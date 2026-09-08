from rest_framework import serializers


class ApiUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    role = serializers.CharField()
    display_name = serializers.CharField()
    username = serializers.CharField()
    student_number = serializers.CharField(allow_null=True)
    home_url = serializers.CharField()
    must_change_password = serializers.BooleanField()


class SessionContextSerializer(serializers.Serializer):
    user = ApiUserSerializer()


class LoginRequestSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(trim_whitespace=False)


class CsrfTokenSerializer(serializers.Serializer):
    csrf_token = serializers.CharField()


class StudentPasswordChangeSerializer(serializers.Serializer):
    new_password1 = serializers.CharField(min_length=8, trim_whitespace=False, write_only=True)
    new_password2 = serializers.CharField(min_length=8, trim_whitespace=False, write_only=True)


class HealthSerializer(serializers.Serializer):
    status = serializers.CharField()
