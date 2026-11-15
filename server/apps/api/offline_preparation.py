"""Short-lived, cookie-independent identity checks for assessment workers."""
import base64
import json
from importlib import import_module

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.contrib.auth import get_user
from django.http import HttpRequest
from django.utils.crypto import salted_hmac
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.assessment.models import StudentQuestionAssignment
from .exceptions import ApiBusinessError
from .views import AuthenticationView


class PreparationScopeSerializer(serializers.Serializer):
    session_id = serializers.IntegerField(min_value=1)
    assignment_id = serializers.IntegerField(min_value=1)


class PreparationProofSerializer(serializers.Serializer):
    proof = serializers.CharField(max_length=2048)


class VerifiedPreparationSerializer(PreparationScopeSerializer):
    user_id = serializers.IntegerField()


def proof_cipher():
    key = salted_hmac('kt.offline-preparation.v1', 'session-proof', algorithm='sha256').digest()
    return Fernet(base64.urlsafe_b64encode(key))


class OfflinePreparationView(AuthenticationView):
    @extend_schema(request=PreparationScopeSerializer, responses=PreparationProofSerializer)
    def post(self, request):
        serializer = PreparationScopeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        scope = serializer.validated_data
        if not StudentQuestionAssignment.objects.filter(
            pk=scope['assignment_id'], session_id=scope['session_id'], session__student=request.user,
        ).exists():
            raise ApiBusinessError('作答身份与题目不一致。', code='OFFLINE_SCOPE_MISMATCH', status=403)
        # Encrypt, rather than sign in cleartext: the HttpOnly session key must
        # never become a usable cookie in page JavaScript or a cache envelope.
        payload = {**scope, 'user_id': request.user.pk, 'session_key': request.session.session_key}
        proof = proof_cipher().encrypt(json.dumps(payload).encode()).decode()
        return Response({'proof': proof})


class VerifyOfflinePreparationView(AuthenticationView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(request=PreparationProofSerializer, responses=VerifiedPreparationSerializer)
    def post(self, request):
        serializer = PreparationProofSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            payload = json.loads(proof_cipher().decrypt(serializer.validated_data['proof'].encode(), ttl=60))
        except (InvalidToken, ValueError):
            raise ApiBusinessError('准备凭据已失效，请重新准备。', code='OFFLINE_PROOF_INVALID', status=401)
        original = HttpRequest()
        original.session = import_module(settings.SESSION_ENGINE).SessionStore(payload['session_key'])
        user = get_user(original)
        if not user.is_authenticated or user.pk != payload['user_id']:
            raise ApiBusinessError('登录已失效，请重新登录。', code='AUTHENTICATION_REQUIRED', status=401)
        # This response confirms identity/scope only; it cannot authorize writes.
        return Response({key: payload[key] for key in ('user_id', 'session_id', 'assignment_id')})
