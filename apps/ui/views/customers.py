"""Customer views — list, create, edit, archive."""

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from apps.billing.models import Customer
from apps.billing.services.customer_service import enrich_customer_data
from apps.ui.views.helpers import build_address_from_post, build_electronic_address


@login_required
def customer_list(request):
    org = request.organization
    if not org:
        return redirect("ui:dashboard")

    show_archived = request.GET.get("archived") == "1"
    customers = Customer.objects.filter(organization=org).order_by("name")
    if not show_archived:
        customers = customers.filter(archived=False)
    search = request.GET.get("search")
    if search:
        customers = customers.filter(
            Q(name__icontains=search) | Q(siren__icontains=search)
        )

    paginator = Paginator(customers, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    if request.headers.get("HX-Request"):
        return render(
            request,
            "ui/partials/customer_table.html",
            {"customers": page_obj, "page_obj": page_obj},
        )

    return render(
        request,
        "ui/customer_list.html",
        {"customers": page_obj, "page_obj": page_obj, "show_archived": show_archived},
    )


@login_required
def customer_create(request):
    org = request.organization
    if request.method == "POST":
        data = {
            "name": request.POST.get("name", ""),
            "siren": request.POST.get("siren", ""),
            "siret": request.POST.get("siret", ""),
            "vat_number": request.POST.get("vat_number", ""),
            "customer_type": request.POST.get("customer_type", ""),
            "email": request.POST.get("email", ""),
            "external_id": request.POST.get("external_id") or None,
            "electronic_address": build_electronic_address(request.POST),
            "address": build_address_from_post(request.POST),
        }
        enrich_customer_data(data)
        Customer.objects.create(organization=org, **data)
        return redirect("ui:customer_list")
    return render(request, "ui/customer_form.html")


@login_required
def customer_edit(request, uuid):
    org = request.organization
    customer = get_object_or_404(Customer, uuid=uuid, organization=org)
    if request.method == "POST":
        data = {
            "name": request.POST.get("name", customer.name),
            "siren": request.POST.get("siren", customer.siren),
            "siret": request.POST.get("siret", customer.siret),
            "vat_number": request.POST.get("vat_number", customer.vat_number),
            "customer_type": request.POST.get("customer_type", ""),
            "email": request.POST.get("email", customer.email),
            "external_id": request.POST.get("external_id") or customer.external_id,
            "electronic_address": build_electronic_address(request.POST),
            "address": build_address_from_post(request.POST),
        }
        enrich_customer_data(data)
        for key, value in data.items():
            setattr(customer, key, value)
        customer.save()
        return redirect("ui:customer_list")
    return render(request, "ui/customer_form.html", {"customer": customer})


@login_required
def customer_archive(request, uuid):
    customer = get_object_or_404(Customer, uuid=uuid, organization=request.organization)
    if request.method == "POST":
        customer.archived = not customer.archived
        customer.save(update_fields=["archived"])
    return redirect("ui:customer_list")
