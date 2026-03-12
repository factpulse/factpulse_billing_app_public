"""Dashboard and guide views."""

import markdown
from django.conf import settings as django_settings
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.shortcuts import render
from django.utils.safestring import mark_safe

from apps.billing.models import Invoice

S = Invoice.Status


@login_required
def guide(request):
    md_path = django_settings.BASE_DIR / "docs" / "portal-guide.md"
    html_content = ""
    if md_path.exists():
        html_content = markdown.markdown(
            md_path.read_text(encoding="utf-8"),
            extensions=["tables", "fenced_code"],
        )
    return render(request, "ui/guide.html", {"guide_html": mark_safe(html_content)})  # nosec B308 B703 — static file, not user input


@login_required
def dashboard(request):
    org = request.organization
    if not org:
        return render(request, "ui/no_org.html")

    invoices = Invoice.objects.filter(organization=org, deleted_at__isnull=True)

    stats = invoices.aggregate(
        total_count=Count("id"),
        draft_count=Count("id", filter=Q(status=S.DRAFT)),
        validated_count=Count("id", filter=Q(status=S.VALIDATED)),
        transmitted_count=Count("id", filter=Q(status=S.TRANSMITTED)),
        paid_count=Count("id", filter=Q(status=S.PAID)),
        total_amount=Sum("total_incl_tax"),
        pending_amount=Sum(
            "total_incl_tax",
            filter=Q(status__in=[S.VALIDATED, S.TRANSMITTED, S.ACCEPTED]),
        ),
    )

    recent_invoices = invoices.select_related("supplier", "customer")[:10]

    return render(
        request,
        "ui/dashboard.html",
        {
            "stats": stats,
            "recent_invoices": recent_invoices,
        },
    )
