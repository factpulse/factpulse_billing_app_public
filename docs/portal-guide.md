# Portal Guide

The FactPulse Billing App includes a web portal for managing invoices, clients, suppliers, and products.

## Getting Started

### Signup

From the signup page, create an account by providing:

- Email address
- Password
- Organization name

This creates your user account and organization. You are automatically logged in as the **owner** of the organization.

### Login

Sign in with your email and password.

## Dashboard

The dashboard shows an overview of your invoicing activity:

- **Total invoices**: All invoices in the organization
- **Draft**: Invoices being prepared
- **Validated**: Invoices with generated Factur-X PDFs
- **Transmitted**: Invoices sent to the certified platform
- **Paid**: Completed invoices
- **Pending amount**: Total amount of unpaid invoices

## Managing Suppliers

Suppliers represent the legal entities issuing invoices (the seller on the invoice).

### Create a supplier

1. Go to **Suppliers** in the navigation
2. Click **New Supplier**
3. Fill in the required fields:
   - Name (BT-27)
   - SIREN (BT-30), SIRET (BT-29)
   - VAT number (BT-31)
   - IBAN/BIC for payment
   - Email, postal address
4. Optionally configure:
   - Logo and primary color (for PDF branding)
   - Legal mentions (appears on PDF footer)
   - Default payment terms (days, end of month)
   - Mandatory notes (late payment penalties, etc.)

### Supplier settings

The supplier settings page lets you customize the numbering sequence (prefix template and padding) used for invoice numbers.

## Managing Customers

Customers are optional records for convenience. They are used to pre-fill recipient data on invoices.

### Create a customer

1. Go to **Customers** in the navigation
2. Click **New Customer**
3. Fill in name, SIREN, SIRET, email, address

### SIRENE import

When creating a customer, you can search the SIRENE directory by SIREN number. The system will auto-fill company details from the official French business registry.

### Archive/Unarchive

Customers can be archived (hidden from lists) without deleting them. Archived customers still appear on their existing invoices.

## Managing Products

Products are a convenience catalog. When creating invoices, you can reference products to auto-fill line item defaults.

### Create a product

1. Go to **Products** in the navigation
2. Click **New Product**
3. Fill in:
   - Name
   - Default unit price
   - Default VAT rate and category
   - Unit code (UN/ECE Rec 20, e.g., `C62` for unit, `HUR` for hour)
   - Reference and description

## Creating and Managing Invoices

### Create an invoice

1. Go to **Invoices** > **New Invoice**
2. Select a **supplier** (who issues the invoice)
3. Select or create a **customer** (who receives it)
4. Add **line items**:
   - Select a product from the catalog, or enter details manually
   - Set quantity, unit price, and VAT rate
5. The calculator automatically computes totals (net, VAT, gross)
6. Set dates (issue date, due date) and payment terms
7. Click **Save** to create a draft

### Invoice lifecycle

| Status | Description | Available actions |
|--------|-------------|-------------------|
| **Draft** | Being edited | Edit, Delete, Validate |
| **Processing** | Factur-X PDF being generated | Wait |
| **Validated** | PDF generated, ready to send | Transmit, Mark paid, Cancel |
| **Transmitted** | Sent to certified platform | Mark paid, Cancel |
| **Accepted** | Accepted by recipient | Mark paid |
| **Rejected** | Rejected by recipient | - |
| **Paid** | Payment received | Cancel |
| **Cancelled** | Credit note issued | - |

### Validate an invoice

Click **Validate** on a draft invoice. This:
1. Assigns a sequential invoice number
2. Sets the issue date (if not already set)
3. Sends the data to FactPulse for Factur-X PDF generation
4. Transitions the invoice to `processing`, then `validated`

### Transmit

Click **Transmit** on a validated invoice to send it to the certified platform. The transmission status is polled automatically every 15 minutes.

### Mark as paid

Click **Mark paid** to record payment. Optionally provide a payment date, reference, and amount.

### Cancel an invoice

Click **Cancel** to create a credit note. This creates a new draft invoice of type 381 linked to the original. If the credit note total matches the original, the original is auto-cancelled when the credit note is validated.

### Generate a payment link (Stripe)

> This feature is only available when `STRIPE_ENABLED=true` and Stripe is configured.

On a validated, transmitted, or accepted invoice, click the **Payment link** button. This generates a one-time Stripe Checkout URL and copies it to your clipboard. Send this link to your customer by email, chat, or any channel.

When the customer pays via the link, the invoice is **automatically marked as paid** — no manual action needed.

To configure Stripe:

1. Go to **Settings** > **Payments** (Owner only)
2. Enter your Stripe API key and webhook secret
3. Optionally set a **default supplier** — this enables automatic invoice creation from Stripe subscriptions
4. Configure your Stripe Dashboard to send webhooks to `https://your-domain.com/api/v1/payments/webhooks/stripe/`

#### Stripe Subscriptions (automatic invoicing)

If a default supplier is configured, invoices from Stripe Billing subscriptions are automatically converted into Factur-X invoices, validated, and marked as paid — with zero manual intervention.

#### Other payment providers

The app also supports **GoCardless** (SEPA Direct Debit) and **Fintecture** (instant bank transfer via Open Banking). Configure them via the API — see the [API Guide](api-guide.md) for details.

## Transmission Settings (Owner only)

Owners can configure the certified platform connection settings:

1. Go to **Settings** > **PDP Settings**
2. View or update the configuration pushed to FactPulse
3. This is only available if the organization has been provisioned with a FactPulse client

If FactPulse is not configured, a banner indicates that the app is running in degraded mode.

## API Keys & Claude Integration

FactPulse can be used directly from Claude (Anthropic's AI assistant) via the MCP protocol. Claude can then create invoices, look up customers, query your dashboard stats, etc., through natural conversation.

### Claude Desktop

1. Open Claude Desktop → **Settings** → **Connectors**
2. Click **Add custom connector**
3. Enter the URL: `https://your-domain.example.com/mcp/mcp`
4. Your browser opens — log in with your FactPulse account and click **Authorize**
5. Done — 26 billing tools are available in Claude

### Claude Code (CLI)

Claude Code uses an API key for authentication.

1. Go to **API Keys** in the sidebar
2. Click **Create key** and give it a name (e.g. "Claude Code")
3. **Copy the key immediately** — it starts with `fp_` and won't be shown again
4. Configure Claude Code with this key (see `docs/mcp-guide.md` for details)

### Managing API keys

- Keys are stored hashed (SHA-256) — they are only visible at creation time
- Each key is linked to a user and an organization
- Revoke a key from the **API Keys** page or via `DELETE /api/v1/auth/api-keys/<uuid>/`
- The `fp_XXXXXXXX…` prefix helps identify which key is in use
- Last usage date is visible in the interface

## Multi-Organization

Users can belong to multiple organizations with different roles:

| Role | Permissions |
|------|-------------|
| **Owner** | Full access: manage members, transmission settings, webhooks, all CRUD operations |
| **Member** | Create and manage invoices, suppliers, customers, products |
| **Viewer** | Read-only access to invoices and data |
| **Customer Access** | View invoices addressed to their company only |

### Switching organizations

If you belong to multiple organizations, use the organization switcher in the navigation to switch context. All data is scoped to the current organization.
