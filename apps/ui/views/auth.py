"""Authentication views — login, signup, email verification, logout."""

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.shortcuts import redirect, render
from django.utils.encoding import force_str
from django.utils.http import url_has_allowed_host_and_scheme, urlsafe_base64_decode

from apps.core.models import OrganizationMembership
from apps.core.services import create_account, send_verification_email
from apps.core.tokens import email_verification_token


def login_view(request):
    if request.user.is_authenticated:
        return redirect("ui:dashboard")

    email_not_verified = False
    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            login(request, form.get_user())
            return redirect("ui:dashboard")
        else:
            # Check if the failure is due to unverified email.
            username = request.POST.get("username", "").strip().lower()
            try:
                user = User.objects.get(username=username)
                if (
                    user.check_password(request.POST.get("password", ""))
                    and hasattr(user, "profile")
                    and not user.profile.email_verified
                ):
                    email_not_verified = True
            except User.DoesNotExist:
                pass
    return render(
        request,
        "ui/login.html",
        {"form": form, "email_not_verified": email_not_verified},
    )


def signup_view(request):
    if request.user.is_authenticated:
        return redirect("ui:dashboard")

    if request.method == "POST":
        email = request.POST.get("email", "")
        password = request.POST.get("password", "")
        password_confirm = request.POST.get("password_confirm", "")
        org_name = request.POST.get("org_name", "")

        if password != password_confirm:
            return render(
                request,
                "ui/signup.html",
                {
                    "error": "Les mots de passe ne correspondent pas.",
                    "form_data": request.POST,
                },
            )

        if len(password) < 8:
            return render(
                request,
                "ui/signup.html",
                {
                    "error": "Le mot de passe doit contenir au moins 8 caractères.",
                    "form_data": request.POST,
                },
            )

        try:
            user, org = create_account(email, password, org_name)
            send_verification_email(user, request)
            return redirect("ui:verify_email_sent")
        except ValueError as e:
            return render(
                request,
                "ui/signup.html",
                {
                    "error": str(e),
                    "form_data": request.POST,
                },
            )

    return render(request, "ui/signup.html")


def verify_email_sent(request):
    return render(request, "ui/verify_email_sent.html")


def verify_email_view(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is not None and email_verification_token.check_token(user, token):
        user.profile.email_verified = True
        user.profile.save(update_fields=["email_verified"])
        return render(request, "ui/verify_email_done.html")

    return render(request, "ui/verify_email_done.html", {"invalid": True})


def resend_verification_view(request):
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        try:
            user = User.objects.get(email=email)
            if not user.profile.email_verified:
                send_verification_email(user, request)
        except User.DoesNotExist:
            pass
        # Always show same message to prevent email enumeration.
        messages.success(
            request,
            "Si un compte existe avec cette adresse, un email de vérification a été envoyé.",
        )
        return redirect("ui:verify_email_sent")
    return redirect("ui:login")


def logout_view(request):
    logout(request)
    return redirect("ui:login")


@login_required
def switch_org(request):
    if request.method == "POST":
        org_id = request.POST.get("organization_id")
        if org_id:
            # Verify membership
            if OrganizationMembership.objects.filter(
                user=request.user, organization_id=org_id
            ).exists():
                request.session["organization_id"] = int(org_id)
    referer = request.META.get("HTTP_REFERER")
    if referer:
        if url_has_allowed_host_and_scheme(referer, allowed_hosts={request.get_host()}):
            return redirect(referer)
    return redirect("ui:dashboard")
