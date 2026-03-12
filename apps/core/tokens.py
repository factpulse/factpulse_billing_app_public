from django.contrib.auth.tokens import PasswordResetTokenGenerator


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    key_salt = "apps.core.tokens.EmailVerificationTokenGenerator"

    def _make_hash_value(self, user, timestamp):
        # Include email_verified status so the token is invalidated once used.
        verified = getattr(getattr(user, "profile", None), "email_verified", False)
        return f"{user.pk}{timestamp}{verified}"


email_verification_token = EmailVerificationTokenGenerator()
