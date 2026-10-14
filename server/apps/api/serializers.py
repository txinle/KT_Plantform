from rest_framework import serializers


class ApiUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    role = serializers.CharField()
    display_name = serializers.CharField()
    username = serializers.CharField()
    student_number = serializers.CharField(allow_null=True)
    home_url = serializers.CharField()
    uses_initial_password = serializers.BooleanField()
    initial_password_reminder_pending = serializers.BooleanField()
    research_consent_required = serializers.BooleanField()
    research_consent_version = serializers.CharField()


class SessionContextSerializer(serializers.Serializer):
    user = ApiUserSerializer()


class BootstrapContextSerializer(serializers.Serializer):
    csrf_token = serializers.CharField()
    user = ApiUserSerializer(allow_null=True)


class LoginContextSerializer(SessionContextSerializer):
    csrf_token = serializers.CharField()


class LoginRequestSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(trim_whitespace=False)


class CsrfTokenSerializer(serializers.Serializer):
    csrf_token = serializers.CharField()


class StudentPasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(trim_whitespace=False, write_only=True)
    new_password1 = serializers.CharField(min_length=8, trim_whitespace=False, write_only=True)
    new_password2 = serializers.CharField(min_length=8, trim_whitespace=False, write_only=True)


class ResearchConsentDocumentSerializer(serializers.Serializer):
    version = serializers.CharField()
    title = serializers.CharField()
    paragraphs = serializers.ListField(child=serializers.CharField())
    researcher_name = serializers.CharField()
    researcher_email = serializers.EmailField()
    checkbox_label = serializers.CharField()
    exit_label = serializers.CharField()
    accept_label = serializers.CharField()


class ResearchConsentAcceptanceSerializer(serializers.Serializer):
    version = serializers.CharField()
    accepted = serializers.BooleanField()

    def validate_accepted(self, value):
        if value is not True:
            raise serializers.ValidationError("必须明确同意后方可进入答题。")
        return value


class HealthSerializer(serializers.Serializer):
    status = serializers.CharField()
