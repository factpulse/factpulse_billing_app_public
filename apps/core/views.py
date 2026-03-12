from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.core.models import APIKey
from apps.core.serializers import EmailTokenObtainPairSerializer, RegisterSerializer
from apps.core.services import create_account, send_verification_email
from apps.core.throttling import AuthRateThrottle


class EmailTokenObtainPairView(TokenObtainPairView):
    """JWT token endpoint that accepts 'email' + 'password'."""

    serializer_class = EmailTokenObtainPairSerializer
    throttle_classes = [AuthRateThrottle]


class RegisterView(APIView):
    """POST /api/v1/auth/register/ — create account and return JWT pair."""

    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]
    serializer_class = RegisterSerializer

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            user, org = create_account(
                email=serializer.validated_data["email"],
                password=serializer.validated_data["password"],
                org_name=serializer.validated_data["org_name"],
            )
        except ValueError as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        send_verification_email(user, request)
        return Response(
            {"detail": "Verification email sent. Check your inbox."},
            status=status.HTTP_201_CREATED,
        )


class LogoutView(APIView):
    """POST /api/v1/auth/logout/ — blacklist the refresh token."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh = request.data.get("refresh")
        if not refresh:
            return Response(
                {"detail": "Refresh token is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            RefreshToken(refresh).blacklist()
        except TokenError:
            return Response(
                {"detail": "Invalid or expired token."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(status=status.HTTP_205_RESET_CONTENT)


# ── API Keys ──────────────────────────────────────────────────────────


class APIKeyCreateSerializer(drf_serializers.Serializer):
    name = drf_serializers.CharField(max_length=255)


class APIKeyListSerializer(drf_serializers.ModelSerializer):
    class Meta:
        model = APIKey
        fields = [
            "uuid",
            "name",
            "prefix",
            "is_active",
            "last_used_at",
            "expires_at",
            "created_at",
        ]


class APIKeyListCreateView(APIView):
    """GET: list API keys. POST: create a new one."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        org = request.organization
        if not org:
            return Response({"detail": "No organization."}, status=400)
        keys = APIKey.objects.filter(user=request.user, organization=org)
        return Response(APIKeyListSerializer(keys, many=True).data)

    def post(self, request):
        org = request.organization
        if not org:
            return Response({"detail": "No organization."}, status=400)
        serializer = APIKeyCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        api_key, raw_key = APIKey.generate(
            name=serializer.validated_data["name"],
            user=request.user,
            organization=org,
        )
        data = APIKeyListSerializer(api_key).data
        data["key"] = raw_key  # Only time the full key is returned
        return Response(data, status=status.HTTP_201_CREATED)


class APIKeyRevokeView(APIView):
    """DELETE /api/v1/auth/api-keys/<uuid>/ — revoke an API key."""

    permission_classes = [IsAuthenticated]

    def delete(self, request, uuid):
        org = request.organization
        try:
            api_key = APIKey.objects.get(uuid=uuid, user=request.user, organization=org)
        except APIKey.DoesNotExist:
            return Response({"detail": "API key not found."}, status=404)

        api_key.is_active = False
        api_key.save(update_fields=["is_active"])
        return Response(status=status.HTTP_204_NO_CONTENT)
