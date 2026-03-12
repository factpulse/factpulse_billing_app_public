from django.urls import path
from oauth2_provider.views import RevokeTokenView, TokenView

from apps.oauth.views import AuthorizationView, dynamic_client_registration

app_name = "oauth"

urlpatterns = [
    path("authorize/", AuthorizationView.as_view(), name="authorize"),
    path("token/", TokenView.as_view(), name="token"),
    path("revoke_token/", RevokeTokenView.as_view(), name="revoke-token"),
    path("register/", dynamic_client_registration, name="register"),
]
