from rest_framework import serializers


class ApiUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    role = serializers.CharField()
    display_name = serializers.CharField()


class SessionContextSerializer(serializers.Serializer):
    user = ApiUserSerializer()


class CsrfTokenSerializer(serializers.Serializer):
    csrf_token = serializers.CharField()


class HealthSerializer(serializers.Serializer):
    status = serializers.CharField()
