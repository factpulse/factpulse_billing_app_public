"""Custom authentication backend that accepts ``email`` as credential."""

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

User = get_user_model()


class EmailBackend(ModelBackend):
    """Authenticate using *email* instead of *username*.

    SimpleJWT's ``EmailTokenObtainPairSerializer`` calls
    ``authenticate(email=…, password=…)`` — Django's default ``ModelBackend``
    only accepts ``username``, so this backend bridges the gap.
    """

    def authenticate(self, request, email=None, password=None, **kwargs):
        username = email or kwargs.get("username")
        if username is None or password is None:
            return None
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            # Run the password hasher to prevent timing attacks.
            User().set_password(password)
            return None
        if not user.check_password(password) or not self.user_can_authenticate(user):
            return None

        # Reject users who haven't verified their email.
        profile = getattr(user, "profile", None)
        if profile is not None and not profile.email_verified:
            return None

        return user
