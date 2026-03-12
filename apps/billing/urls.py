from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.billing.views import (
    CustomerViewSet,
    InvoiceViewSet,
    ProductViewSet,
    SupplierViewSet,
)

router = DefaultRouter()
router.register("suppliers", SupplierViewSet, basename="supplier")
router.register("customers", CustomerViewSet, basename="customer")
router.register("products", ProductViewSet, basename="product")
router.register("invoices", InvoiceViewSet, basename="invoice")

urlpatterns = [
    path("", include(router.urls)),
]
