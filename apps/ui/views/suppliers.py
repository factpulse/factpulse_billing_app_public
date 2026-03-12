"""Supplier views — list, create, edit, settings, defaults, archive."""

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.billing.models import Supplier
from apps.ui.views.helpers import build_address_from_post, build_electronic_address


@login_required
def supplier_list(request):
    org = request.organization
    if not org:
        return redirect("ui:dashboard")

    show_archived = request.GET.get("archived") == "1"
    suppliers = Supplier.objects.filter(organization=org).order_by("name")
    if not show_archived:
        suppliers = suppliers.filter(archived=False)

    paginator = Paginator(suppliers, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    if request.headers.get("HX-Request"):
        return render(
            request,
            "ui/partials/supplier_table.html",
            {"suppliers": page_obj, "page_obj": page_obj},
        )

    return render(
        request,
        "ui/supplier_list.html",
        {"suppliers": page_obj, "page_obj": page_obj, "show_archived": show_archived},
    )


@login_required
def supplier_create(request):
    org = request.organization
    if request.method == "POST":
        Supplier.objects.create(
            organization=org,
            name=request.POST.get("name", ""),
            siren=request.POST.get("siren", ""),
            siret=request.POST.get("siret", ""),
            vat_number=request.POST.get("vat_number", ""),
            email=request.POST.get("email", ""),
            external_id=request.POST.get("external_id") or None,
            is_default=request.POST.get("is_default") == "on",
            electronic_address=build_electronic_address(request.POST),
            address=build_address_from_post(request.POST),
        )
        return redirect("ui:supplier_list")
    return render(request, "ui/supplier_form.html")


@login_required
def supplier_edit(request, uuid):
    org = request.organization
    supplier = get_object_or_404(Supplier, uuid=uuid, organization=org)
    if request.method == "POST":
        supplier.name = request.POST.get("name", supplier.name)
        supplier.siren = request.POST.get("siren", supplier.siren)
        supplier.siret = request.POST.get("siret", supplier.siret)
        supplier.vat_number = request.POST.get("vat_number", supplier.vat_number)
        supplier.email = request.POST.get("email", supplier.email)
        supplier.external_id = request.POST.get("external_id") or supplier.external_id
        supplier.is_default = request.POST.get("is_default") == "on"
        supplier.electronic_address = build_electronic_address(request.POST)
        supplier.address = build_address_from_post(request.POST)
        supplier.save()
        return redirect("ui:supplier_list")
    return render(request, "ui/supplier_form.html", {"supplier": supplier})


@login_required
def supplier_settings(request, uuid):
    supplier = get_object_or_404(Supplier, uuid=uuid, organization=request.organization)
    if request.method == "POST":
        supplier.note_pmt = request.POST.get("note_pmt", supplier.note_pmt)
        supplier.note_pmd = request.POST.get("note_pmd", supplier.note_pmd)
        supplier.note_aab = request.POST.get("note_aab", supplier.note_aab)
        supplier.pdf_legal_mentions = request.POST.get("pdf_legal_mentions", "")
        supplier.primary_color = request.POST.get("primary_color", "")
        supplier.iban = request.POST.get("iban", "").strip()
        supplier.bic = request.POST.get("bic", "").strip()
        days = request.POST.get("payment_terms_days")
        supplier.payment_terms_days = int(days) if days else None
        supplier.payment_terms_end_of_month = (
            request.POST.get("payment_terms_end_of_month") == "on"
        )
        vat_regime = request.POST.get("vat_regime", "")
        if vat_regime in Supplier.VatRegime.values:
            supplier.vat_regime = vat_regime
        if request.FILES.get("logo"):
            supplier.logo = request.FILES["logo"]
        if request.POST.get("logo_clear") == "on":
            supplier.logo = ""
        supplier.save()
        return redirect("ui:supplier_settings", uuid=uuid)
    return render(request, "ui/supplier_settings.html", {"supplier": supplier})


@login_required
def supplier_defaults(request, uuid):
    supplier = get_object_or_404(Supplier, uuid=uuid, organization=request.organization)
    return JsonResponse(
        {
            "note_pmt": supplier.note_pmt,
            "note_pmd": supplier.note_pmd,
            "note_aab": supplier.note_aab,
            "payment_terms_days": supplier.payment_terms_days,
            "payment_terms_end_of_month": supplier.payment_terms_end_of_month,
        }
    )


@login_required
def supplier_archive(request, uuid):
    supplier = get_object_or_404(Supplier, uuid=uuid, organization=request.organization)
    if request.method == "POST":
        supplier.archived = not supplier.archived
        supplier.save(update_fields=["archived"])
    return redirect("ui:supplier_list")
