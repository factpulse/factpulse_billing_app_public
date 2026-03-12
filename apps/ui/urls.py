from django.contrib.auth import views as auth_views
from django.urls import path

from apps.ui import views

app_name = "ui"

urlpatterns = [
    # Auth
    path("login/", views.login_view, name="login"),
    path("signup/", views.signup_view, name="signup"),
    path("logout/", views.logout_view, name="logout"),
    path(
        "password-reset/",
        auth_views.PasswordResetView.as_view(
            template_name="ui/password_reset.html",
            email_template_name="registration/password_reset_email.html",
            success_url="/password-reset/done/",
        ),
        name="password_reset",
    ),
    path(
        "password-reset/done/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="ui/password_reset_done.html",
        ),
        name="password_reset_done",
    ),
    path(
        "password-reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="ui/password_reset_confirm.html",
            success_url="/password-reset/complete/",
        ),
        name="password_reset_confirm",
    ),
    path(
        "password-reset/complete/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="ui/password_reset_complete.html",
        ),
        name="password_reset_complete",
    ),
    path("verify-email/sent/", views.verify_email_sent, name="verify_email_sent"),
    path(
        "verify-email/<uidb64>/<token>/",
        views.verify_email_view,
        name="verify_email",
    ),
    path(
        "resend-verification/",
        views.resend_verification_view,
        name="resend_verification",
    ),
    path("switch-org/", views.switch_org, name="switch_org"),
    # Guide
    path("guide/", views.guide, name="guide"),
    # Dashboard
    path("", views.dashboard, name="dashboard"),
    # Invoices
    path("invoices/", views.invoice_list, name="invoice_list"),
    path("invoices/new/", views.invoice_create, name="invoice_create"),
    path("invoices/<uuid:uuid>/", views.invoice_detail, name="invoice_detail"),
    path("invoices/<uuid:uuid>/edit/", views.invoice_edit, name="invoice_edit"),
    path(
        "invoices/<uuid:uuid>/validate/",
        views.invoice_validate,
        name="invoice_validate",
    ),
    path(
        "invoices/<uuid:uuid>/transmit/",
        views.invoice_transmit,
        name="invoice_transmit",
    ),
    path(
        "invoices/<uuid:uuid>/mark-paid/",
        views.invoice_mark_paid,
        name="invoice_mark_paid",
    ),
    path("invoices/<uuid:uuid>/cancel/", views.invoice_cancel, name="invoice_cancel"),
    path("invoices/<uuid:uuid>/delete/", views.invoice_delete, name="invoice_delete"),
    # Customers
    path("customers/", views.customer_list, name="customer_list"),
    path("customers/new/", views.customer_create, name="customer_create"),
    path("customers/<uuid:uuid>/edit/", views.customer_edit, name="customer_edit"),
    path(
        "customers/<uuid:uuid>/archive/",
        views.customer_archive,
        name="customer_archive",
    ),
    # Products
    path("products/", views.product_list, name="product_list"),
    path("products/new/", views.product_create, name="product_create"),
    path("products/<uuid:uuid>/edit/", views.product_edit, name="product_edit"),
    path(
        "products/<uuid:uuid>/archive/", views.product_archive, name="product_archive"
    ),
    # Suppliers
    path("suppliers/", views.supplier_list, name="supplier_list"),
    path("suppliers/new/", views.supplier_create, name="supplier_create"),
    path("suppliers/<uuid:uuid>/edit/", views.supplier_edit, name="supplier_edit"),
    path(
        "suppliers/<uuid:uuid>/archive/",
        views.supplier_archive,
        name="supplier_archive",
    ),
    path(
        "suppliers/<uuid:uuid>/settings/",
        views.supplier_settings,
        name="supplier_settings",
    ),
    path(
        "suppliers/<uuid:uuid>/defaults/",
        views.supplier_defaults,
        name="supplier_defaults",
    ),
    # Settings
    path("settings/pdp/", views.pdp_settings, name="pdp_settings"),
    path("settings/api-keys/", views.api_key_list, name="api_key_list"),
    # SIRENE lookup
    path("sirene-lookup/", views.sirene_lookup, name="sirene_lookup"),
    # Directory lookup (electronic addresses)
    path("directory-lookup/", views.directory_lookup, name="directory_lookup"),
]
