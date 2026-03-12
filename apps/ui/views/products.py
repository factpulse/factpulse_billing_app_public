"""Product views — list, create, edit, archive."""

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from apps.billing.models import Product


@login_required
def product_list(request):
    org = request.organization
    if not org:
        return redirect("ui:dashboard")

    show_archived = request.GET.get("archived") == "1"
    products = Product.objects.filter(organization=org).order_by("name")
    if not show_archived:
        products = products.filter(archived=False)
    search = request.GET.get("search")
    if search:
        products = products.filter(
            Q(name__icontains=search) | Q(reference__icontains=search)
        )

    paginator = Paginator(products, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    if request.headers.get("HX-Request"):
        return render(
            request,
            "ui/partials/product_table.html",
            {"products": page_obj, "page_obj": page_obj},
        )

    return render(
        request,
        "ui/product_list.html",
        {"products": page_obj, "page_obj": page_obj, "show_archived": show_archived},
    )


@login_required
def product_create(request):
    org = request.organization
    if request.method == "POST":
        Product.objects.create(
            organization=org,
            name=request.POST.get("name", ""),
            description=request.POST.get("description", ""),
            reference=request.POST.get("reference", ""),
            external_id=request.POST.get("external_id") or None,
            default_unit_price=request.POST.get("default_unit_price") or None,
            default_vat_rate=request.POST.get("default_vat_rate") or None,
            default_vat_category=request.POST.get("default_vat_category", "S"),
            default_unit=request.POST.get("default_unit", "C62"),
        )
        return redirect("ui:product_list")
    return render(request, "ui/product_form.html")


@login_required
def product_edit(request, uuid):
    org = request.organization
    product = get_object_or_404(Product, uuid=uuid, organization=org)
    if request.method == "POST":
        product.name = request.POST.get("name", product.name)
        product.description = request.POST.get("description", product.description)
        product.reference = request.POST.get("reference", product.reference)
        product.external_id = request.POST.get("external_id") or product.external_id
        product.default_unit_price = (
            request.POST.get("default_unit_price") or product.default_unit_price
        )
        product.default_vat_rate = (
            request.POST.get("default_vat_rate") or product.default_vat_rate
        )
        product.default_vat_category = request.POST.get(
            "default_vat_category", product.default_vat_category
        )
        product.default_unit = request.POST.get("default_unit", product.default_unit)
        product.save()
        return redirect("ui:product_list")
    return render(request, "ui/product_form.html", {"product": product})


@login_required
def product_archive(request, uuid):
    product = get_object_or_404(Product, uuid=uuid, organization=request.organization)
    if request.method == "POST":
        product.archived = not product.archived
        product.save(update_fields=["archived"])
    return redirect("ui:product_list")
