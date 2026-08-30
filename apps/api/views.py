from django.middleware.csrf import get_token
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import CsrfTokenSerializer, HealthSerializer, SessionContextSerializer


class SessionContextView(APIView):
    @extend_schema(responses=SessionContextSerializer)
    def get(self, request):
        return Response(
            {
                "user": {
                    "id": request.user.pk,
                    "role": request.user.role,
                    "display_name": request.user.shown_name,
                }
            }
        )


class CsrfTokenView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(responses=CsrfTokenSerializer)
    def get(self, request):
        return Response({"csrf_token": get_token(request)})


class HealthView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(responses=HealthSerializer)
    def get(self, request):
        return Response({"status": "ok"})
