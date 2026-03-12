# API Guide

The FactPulse Billing App exposes a REST API for managing invoices, suppliers, customers, products, and webhooks. All endpoints are under `/api/v1/`.

## Authentication

### Register a new account

```python
import requests

resp = requests.post("http://localhost:8000/api/v1/auth/register/", json={
    "email": "user@example.com",
    "password": "securepassword123",
    "org_name": "My Company",
})
data = resp.json()
access_token = data["access"]
refresh_token = data["refresh"]
org_uuid = data["organization"]["uuid"]
```

### Obtain tokens (login)

```python
resp = requests.post("http://localhost:8000/api/v1/auth/token/", json={
    "email": "user@example.com",
    "password": "securepassword123",
})
tokens = resp.json()
access_token = tokens["access"]
refresh_token = tokens["refresh"]
```

### Refresh an expired token

```python
resp = requests.post("http://localhost:8000/api/v1/auth/token/refresh/", json={
    "refresh": refresh_token,
})
access_token = resp.json()["access"]
```

### Using tokens

Include the access token as a Bearer token and specify the organization via the `X-Organization` header:

```python
headers = {
    "Authorization": f"Bearer {access_token}",
    "X-Organization": org_uuid,
}
```

## Suppliers

### Create a supplier

```python
resp = requests.post("http://localhost:8000/api/v1/suppliers/", json={
    "name": "ACME Corp",
    "siren": "123456789",
    "siret": "12345678900010",
    "vat_number": "FR12345678901",
    "email": "contact@acme.com",
}, headers=headers)
supplier = resp.json()
supplier_uuid = supplier["uuid"]
```

### List suppliers

```python
resp = requests.get("http://localhost:8000/api/v1/suppliers/", headers=headers)
suppliers = resp.json()["results"]
```

### Update a supplier

```python
resp = requests.patch(f"http://localhost:8000/api/v1/suppliers/{supplier_uuid}/", json={
    "email": "new-email@acme.com",
}, headers=headers)
```

### Delete a supplier

```python
resp = requests.delete(f"http://localhost:8000/api/v1/suppliers/{supplier_uuid}/",
                        headers=headers)
assert resp.status_code == 204
```

## Customers

### Create a customer

```python
resp = requests.post("http://localhost:8000/api/v1/customers/", json={
    "name": "Client SA",
    "siren": "987654321",
    "email": "billing@client.com",
}, headers=headers)
customer = resp.json()
customer_uuid = customer["uuid"]
```

### Invite a customer (owner only)

Creates a `customer_access` user who can view their invoices through the portal.

```python
resp = requests.post(
    f"http://localhost:8000/api/v1/customers/{customer_uuid}/invite/",
    json={"email": "accountant@client.com"},
    headers=headers,
)
```

## Products

### Create a product

```python
resp = requests.post("http://localhost:8000/api/v1/products/", json={
    "name": "Consulting (1h)",
    "default_unit_price": "150.00",
    "default_vat_rate": "20.00",
    "default_unit": "HUR",
}, headers=headers)
product = resp.json()
product_uuid = product["uuid"]
```

## Invoices

### Create an invoice (referenced supplier)

The simplest pattern: reference an existing supplier by UUID.

```python
resp = requests.post("http://localhost:8000/api/v1/invoices/", json={
    "supplier_id": supplier_uuid,
    "customer_id": customer_uuid,
    "en16931_data": {
        "references": {
            "issueDate": "2026-01-15",
            "dueDate": "2026-02-15",
            "invoiceCurrency": "EUR",
        },
        "invoiceLines": [
            {
                "product_id": product_uuid,
                "quantity": "10",
            }
        ],
        "totals": {
            "totalNetAmount": "1500.00",
            "vatAmount": "300.00",
            "totalGrossAmount": "1800.00",
        },
    },
}, headers=headers)
invoice = resp.json()
invoice_uuid = invoice["uuid"]
```

### Create an invoice (inline supplier)

Provide supplier data inline. The system matches by SIREN or auto-creates.

```python
resp = requests.post("http://localhost:8000/api/v1/invoices/", json={
    "supplier": {
        "name": "New Supplier SAS",
        "siren": "111222333",
    },
    "en16931_data": {
        "invoiceLines": [
            {"itemName": "Service", "quantity": "1", "unitNetPrice": "500.00"}
        ],
        "totals": {
            "totalNetAmount": "500.00",
            "vatAmount": "100.00",
            "totalGrossAmount": "600.00",
        },
    },
}, headers=headers)
```

### Create with supplier override (hybrid)

Reference a supplier but override specific fields for this invoice.

```python
resp = requests.post("http://localhost:8000/api/v1/invoices/", json={
    "supplier_id": supplier_uuid,
    "supplier_override": {
        "email": "special-billing@acme.com",
    },
    "en16931_data": {},
}, headers=headers)
```

### Idempotency

Use the `Idempotency-Key` header to safely retry invoice creation. Duplicate requests within 24 hours return the same response.

```python
resp = requests.post("http://localhost:8000/api/v1/invoices/", json={
    "supplier_id": supplier_uuid,
    "en16931_data": {},
}, headers={
    **headers,
    "Idempotency-Key": "unique-request-id-123",
})
```

### Update an invoice (optimistic locking)

Draft invoices can be updated. You must include the current `version` to prevent concurrent modification conflicts.

```python
resp = requests.patch(f"http://localhost:8000/api/v1/invoices/{invoice_uuid}/", json={
    "version": 1,
    "en16931_data": {
        "references": {"dueDate": "2026-03-15"},
    },
}, headers=headers)
updated = resp.json()
assert updated["version"] == 2
```

### Delete an invoice (soft delete)

Only draft invoices can be deleted. Deleted invoices are excluded from list responses.

```python
resp = requests.delete(f"http://localhost:8000/api/v1/invoices/{invoice_uuid}/",
                        headers=headers)
assert resp.status_code == 204
```

### Validate an invoice

Transitions the invoice from `draft` to `processing`. Assigns an invoice number and triggers Factur-X PDF generation via FactPulse.

```python
resp = requests.post(f"http://localhost:8000/api/v1/invoices/{invoice_uuid}/validate/",
                      headers=headers)
assert resp.json()["status"] == "processing"
```

### Transmit an invoice

Sends a validated invoice to the plateforme agréée for transmission to the recipient.

```python
resp = requests.post(f"http://localhost:8000/api/v1/invoices/{invoice_uuid}/transmit/",
                      headers=headers)
assert resp.json()["status"] == "transmitted"
```

### Mark an invoice as paid

```python
resp = requests.post(f"http://localhost:8000/api/v1/invoices/{invoice_uuid}/mark-paid/",
                      json={
                          "payment_date": "2026-02-01",
                          "payment_reference": "VIR-2026-001",
                          "amount": "1800.00",
                      }, headers=headers)
assert resp.json()["status"] == "paid"
```

### Cancel an invoice (create credit note)

Creates a credit note (type 381) linked to the original invoice.

```python
resp = requests.post(f"http://localhost:8000/api/v1/invoices/{invoice_uuid}/cancel/",
                      headers=headers)
credit_note = resp.json()
assert credit_note["en16931_data"]["references"]["invoiceType"] == "381"
```

### Get invoice PDF

```python
resp = requests.get(f"http://localhost:8000/api/v1/invoices/{invoice_uuid}/pdf/",
                     headers=headers)
if resp.status_code == 200:
    with open("invoice.pdf", "wb") as f:
        f.write(resp.content)
elif resp.status_code == 202:
    # PDF is being generated, retry later
    pass
```

### Get audit log

```python
resp = requests.get(f"http://localhost:8000/api/v1/invoices/{invoice_uuid}/audit-log/",
                     headers=headers)
for entry in resp.json():
    print(f"{entry['timestamp']}: {entry['action']} ({entry['old_status']} -> {entry['new_status']})")
```

## Webhooks

### Create a webhook endpoint

```python
resp = requests.post("http://localhost:8000/api/v1/webhooks/", json={
    "url": "https://your-app.example.com/webhook",
    "secret": "your-webhook-secret",
    "events": ["invoice.validated", "invoice.transmitted"],
}, headers=headers)
endpoint = resp.json()
endpoint_uuid = endpoint["uuid"]
# Note: "secret" is write-only and will not appear in responses
```

### Webhook events

| Event | Description |
|-------|-------------|
| `invoice.validated` | Invoice Factur-X PDF generated successfully |
| `invoice.transmitted` | Invoice sent to plateforme agréée |
| `invoice.accepted` | Plateforme agréée accepted the invoice |
| `invoice.rejected` | Plateforme agréée rejected the invoice |
| `invoice.error` | Error during validation/generation |

An empty `events` array subscribes to all events.

### Verifying HMAC-SHA256 signatures

Each webhook delivery includes an `X-Webhook-Signature` header containing an HMAC-SHA256 hex digest of the request body signed with your endpoint secret.

```python
import hashlib
import hmac

def verify_webhook(request_body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(
        secret.encode("utf-8"),
        request_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

### List deliveries

```python
resp = requests.get(f"http://localhost:8000/api/v1/webhooks/{endpoint_uuid}/deliveries/",
                     headers=headers)
for delivery in resp.json():
    print(f"{delivery['event']}: {'OK' if delivery['success'] else 'FAILED'}")
```

**Retry policy**: Failed deliveries are retried up to 3 times with exponential backoff (10s, 60s, 300s). After 3 consecutive failures, the endpoint is automatically deactivated.

## Payments (optional — Stripe)

> Requires `STRIPE_ENABLED=true` and `uv sync --extra stripe`.
> All payment endpoints are under `/api/v1/payments/`.

### Configure Stripe

```python
resp = requests.post("http://localhost:8000/api/v1/payments/providers/", json={
    "provider": "stripe",
    "api_key": "sk_live_...",
    "webhook_secret": "whsec_...",
}, headers=headers)
# Returns provider config (api_key is never returned in responses)
```

### Generate a payment link

```python
resp = requests.post(
    f"http://localhost:8000/api/v1/payments/invoices/{invoice_uuid}/checkout/",
    json={
        "provider": "stripe",
        "success_url": "https://your-app.example.com/payment/success",  # optional
        "cancel_url": "https://your-app.example.com/invoices/123",       # optional
    },
    headers=headers,
)
data = resp.json()
checkout_url = data["checkout_url"]  # Send this URL to your customer
# data["transaction"] contains the payment transaction details
```

The checkout URL is a one-time Stripe Checkout Session link. When the customer
pays, Stripe sends a webhook that automatically calls `mark_paid()` on the
invoice.

### Check payment status

```python
resp = requests.get(
    f"http://localhost:8000/api/v1/payments/invoices/{invoice_uuid}/status/",
    headers=headers,
)
for txn in resp.json():
    print(f"{txn['provider']}: {txn['status']} ({txn['amount']} {txn['currency']})")
```

### Stripe webhook

Configure your Stripe dashboard to send events to:

```
POST https://your-domain.com/api/v1/payments/webhooks/stripe/
```

Events handled:
- `checkout.session.completed` → marks invoice as paid
- `checkout.session.async_payment_succeeded` → marks invoice as paid (SEPA, bank transfer)
- `checkout.session.async_payment_failed` → marks transaction as failed

The webhook is verified via Stripe's HMAC signature (no JWT required).

### Stripe Subscriptions (auto-invoicing)

When configured with a `default_supplier`, the payment app can automatically create Factur-X invoices from Stripe subscription invoices:

1. Configure Stripe with a default supplier:
```python
resp = requests.post("http://localhost:8000/api/v1/payments/providers/", json={
    "provider": "stripe",
    "api_key": "sk_live_...",
    "webhook_secret": "whsec_...",
    "default_supplier": supplier_uuid,  # invoices will be issued by this supplier
}, headers=headers)
```

2. Configure your Stripe Dashboard to send `invoice.finalized` and `invoice.payment_succeeded` events.

3. When a subscription invoice is finalized in Stripe:
   - A draft invoice is auto-created with EN16931-compliant data
   - The invoice is auto-validated (generates Factur-X PDF)
   - When Stripe confirms payment, the invoice is auto-marked as paid

### GoCardless (SEPA Direct Debit)

```python
# Configure GoCardless
resp = requests.post("http://localhost:8000/api/v1/payments/providers/", json={
    "provider": "gocardless",
    "api_key": "live_access_token",
    "webhook_secret": "gc_webhook_secret",
    "config": {"environment": "live"},  # or "sandbox"
}, headers=headers)

# Generate a SEPA checkout link (same endpoint as Stripe)
resp = requests.post(
    f"http://localhost:8000/api/v1/payments/invoices/{invoice_uuid}/checkout/",
    json={"provider": "gocardless"},
    headers=headers,
)
checkout_url = resp.json()["checkout_url"]
```

GoCardless webhook: `POST https://your-domain.com/api/v1/payments/webhooks/gocardless/`

### Fintecture (Open Banking — instant bank transfer)

```python
# Configure Fintecture
resp = requests.post("http://localhost:8000/api/v1/payments/providers/", json={
    "provider": "fintecture",
    "api_key": "your_app_id",
    "webhook_secret": "fint_webhook_secret",
    "config": {"app_secret": "your_app_secret"},
}, headers=headers)

# Generate a bank transfer payment link
resp = requests.post(
    f"http://localhost:8000/api/v1/payments/invoices/{invoice_uuid}/checkout/",
    json={"provider": "fintecture"},
    headers=headers,
)
checkout_url = resp.json()["checkout_url"]
```

Fintecture webhook: `POST https://your-domain.com/api/v1/payments/webhooks/fintecture/`

## Filtering and Pagination

### Cursor pagination

All list endpoints use cursor-based pagination. Responses include `next` and `previous` URLs:

```json
{
    "next": "http://localhost:8000/api/v1/invoices/?cursor=cD0yMDI2...",
    "previous": null,
    "results": [...]
}
```

### Filtering

Invoices support the following filters:

| Parameter | Description |
|-----------|-------------|
| `status` | Filter by status (e.g., `?status=draft`) |
| `supplier` | Filter by supplier UUID or external_id |
| `customer` | Filter by customer UUID or external_id |
| `date_from` | Invoices issued on or after this date |
| `date_to` | Invoices issued on or before this date |

### Search

Use the `search` parameter for full-text search:

```
GET /api/v1/suppliers/?search=ACME
GET /api/v1/invoices/?search=INV-2026
```

## Error Format

All API errors follow a consistent format:

```json
{
    "error": {
        "code": "validation_error",
        "message": "Description of the error.",
        "details": [
            {
                "field": "supplier_id",
                "code": "required",
                "message": "Either supplier_id or supplier is required."
            }
        ]
    }
}
```

Common error codes:

| HTTP Status | Code | Description |
|-------------|------|-------------|
| 400 | `validation_error` | Invalid request payload |
| 401 | `not_authenticated` | Missing or invalid token |
| 403 | `permission_denied` | Insufficient role |
| 404 | `not_found` | Resource not found |
| 409 | `conflict` | State conflict (e.g., editing non-draft invoice) |
| 422 | `validation_error` | Business logic validation failure |

## Invoice Lifecycle

```
draft
  | validate
  v
processing
  | (async: generate Factur-X)
  v
validated  -----> paid
  | transmit       ^
  v                |
transmitted -------+
  | (status poll)
  v
accepted ---------> paid
  or
rejected

Any validated/transmitted/accepted/paid invoice can be cancelled (creates credit note).
```
