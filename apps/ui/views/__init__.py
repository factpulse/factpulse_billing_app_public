"""UI views package — re-exports all view functions for urls.py compatibility."""

from apps.ui.views.api_keys import api_key_list  # noqa: F401
from apps.ui.views.auth import (  # noqa: F401
    login_view,
    logout_view,
    resend_verification_view,
    signup_view,
    switch_org,
    verify_email_sent,
    verify_email_view,
)
from apps.ui.views.customers import (  # noqa: F401
    customer_archive,
    customer_create,
    customer_edit,
    customer_list,
)
from apps.ui.views.dashboard import dashboard, guide  # noqa: F401
from apps.ui.views.invoices import (  # noqa: F401
    _build_invoice_payload,
    invoice_cancel,
    invoice_create,
    invoice_delete,
    invoice_detail,
    invoice_edit,
    invoice_list,
    invoice_mark_paid,
    invoice_transmit,
    invoice_validate,
)
from apps.ui.views.lookups import (  # noqa: F401
    directory_lookup,
    pdp_settings,
    sirene_lookup,
)
from apps.ui.views.products import (  # noqa: F401
    product_archive,
    product_create,
    product_edit,
    product_list,
)
from apps.ui.views.suppliers import (  # noqa: F401
    supplier_archive,
    supplier_create,
    supplier_defaults,
    supplier_edit,
    supplier_list,
    supplier_settings,
)
