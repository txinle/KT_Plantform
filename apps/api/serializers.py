from rest_framework import serializers


class ApiUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    role = serializers.CharField()
    display_name = serializers.CharField()
    username = serializers.CharField()
    student_number = serializers.CharField(allow_null=True)
    home_url = serializers.CharField()


class SessionContextSerializer(serializers.Serializer):
    user = ApiUserSerializer()


class LoginRequestSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(trim_whitespace=False)


class CsrfTokenSerializer(serializers.Serializer):
    csrf_token = serializers.CharField()


class HealthSerializer(serializers.Serializer):
    status = serializers.CharField()
