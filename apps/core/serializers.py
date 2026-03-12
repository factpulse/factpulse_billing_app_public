from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer


class EmailTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Accept 'email' instead of 'username' for JWT login.

    The default Django User model uses username as the auth field.
    Since our usernames ARE emails, this serializer simply maps
    the 'email' field to 'username' for a cleaner API contract.
    """

    username_field = "email"


class RegisterSerializer(serializers.Serializer):
    """Signup: creates user + organization, returns JWT pair."""

    email = serializers.EmailField()
    password = serializers.CharField(min_length=8, write_only=True)
    org_name = serializers.CharField(max_length=255)
