"""Dashboard tools — KPIs and stats."""

from apps.assistant.tools.registry import ParamType, ToolParam, tool


@tool(
    name="get_dashboard_stats",
    description=(
        "Retourne les KPIs de facturation : nombre de factures par statut, "
        "CA total, montant en attente de paiement, factures échues."
    ),
    params=[
        ToolParam(
            "period",
            ParamType.STRING,
            "Période : 'month' (mois en cours), 'quarter', 'year', 'all' (défaut: month)",
            required=False,
            enum=["month", "quarter", "year", "all"],
        ),
    ],
)
def get_dashboard_stats(org, period="month", **kw):
    from datetime import date

    from django.db.models import Count, Q, Sum

    from apps.billing.models import Invoice

    S = Invoice.Status
    qs = Invoice.objects.filter(organization=org, deleted_at__isnull=True)

    # Apply period filter on issue_date
    today = date.today()
    if period == "month":
        start = today.replace(day=1)
        qs = qs.filter(issue_date__gte=start)
    elif period == "quarter":
        quarter_month = ((today.month - 1) // 3) * 3 + 1
        start = today.replace(month=quarter_month, day=1)
        qs = qs.filter(issue_date__gte=start)
    elif period == "year":
        start = today.replace(month=1, day=1)
        qs = qs.filter(issue_date__gte=start)
    # 'all' = no filter

    stats = qs.aggregate(
        total_count=Count("id"),
        draft_count=Count("id", filter=Q(status=S.DRAFT)),
        validated_count=Count("id", filter=Q(status=S.VALIDATED)),
        transmitted_count=Count("id", filter=Q(status=S.TRANSMITTED)),
        paid_count=Count("id", filter=Q(status=S.PAID)),
        cancelled_count=Count("id", filter=Q(status=S.CANCELLED)),
        total_revenue=Sum(
            "total_incl_tax",
            filter=Q(status__in=[S.PAID]),
        ),
        pending_amount=Sum(
            "total_incl_tax",
            filter=Q(status__in=[S.VALIDATED, S.TRANSMITTED, S.ACCEPTED]),
        ),
        overdue_count=Count(
            "id",
            filter=Q(
                due_date__lt=today,
                status__in=[S.VALIDATED, S.TRANSMITTED, S.ACCEPTED],
            ),
        ),
        overdue_amount=Sum(
            "total_incl_tax",
            filter=Q(
                due_date__lt=today,
                status__in=[S.VALIDATED, S.TRANSMITTED, S.ACCEPTED],
            ),
        ),
    )

    return {
        "period": period,
        "total_invoices": stats["total_count"],
        "by_status": {
            "draft": stats["draft_count"],
            "validated": stats["validated_count"],
            "transmitted": stats["transmitted_count"],
            "paid": stats["paid_count"],
            "cancelled": stats["cancelled_count"],
        },
        "total_revenue": str(stats["total_revenue"] or 0),
        "pending_amount": str(stats["pending_amount"] or 0),
        "overdue_count": stats["overdue_count"],
        "overdue_amount": str(stats["overdue_amount"] or 0),
    }
