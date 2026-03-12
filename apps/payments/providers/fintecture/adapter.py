"""Fintecture payment adapter — Open Banking PIS (pay-by-link via bank transfer)."""

import hashlib
import hmac
import json
import logging

import requests as http_requests

from apps.payments.adapters import PaymentProviderAdapter, PaymentResult, WebhookEvent
from apps.webhooks.events import PaymentEvent

logger = logging.getLogger(__name__)

# Fintecture payment status → normalized status
_STATUS_MAP = {
    "payment_created": "pending",
    "payment_pending": "pending",
    "payment_successful": "confirmed",
    "payment_unsuccessful": "failed",
    "payment_error": "failed",
}

_API_BASE = "https://api-sandbox.fintecture.com"


class FintectureAdapter(PaymentProviderAdapter):
    """Fintecture adapter for Open Banking PIS (instant bank transfer)."""

    def __init__(self, api_key, webhook_secret="", app_secret=""):  # nosec B107
        self.api_key = api_key  # app_id
        self.webhook_secret = webhook_secret
        self.app_secret = app_secret

    def _get_access_token(self):
        """Obtain OAuth2 access token from Fintecture."""
        resp = http_requests.post(
            f"{_API_BASE}/oauth/accesstoken",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "client_credentials",
                "app_id": self.api_key,
                "scope": "PIS",
            },
            auth=(self.api_key, self.app_secret),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def create_checkout(
        self,
        *,
        amount,
        currency,
        invoice_uuid,
        invoice_number,
        customer_email,
        success_url,
        cancel_url,
        metadata=None,
    ) -> PaymentResult:
        """Create a Fintecture PIS payment initiation (connect URL)."""
        token = self._get_access_token()

        payload = {
            "meta": {
                "psu_name": customer_email or "Customer",
                "psu_email": customer_email,
            },
            "data": {
                "type": "PIS",
                "attributes": {
                    "amount": str(amount),
                    "currency": currency.upper(),
                    "communication": f"Facture {invoice_number}",
                },
            },
        }

        resp = http_requests.post(
            f"{_API_BASE}/pis/v2/connect",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "x-psu-type": "retail",
            },
            json=payload,
            params={
                "redirect_uri": success_url,
                "state": str(invoice_uuid),
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        connect_url = data.get("meta", {}).get("url", "")
        session_id = data.get("meta", {}).get("session_id", "")

        return PaymentResult(
            provider_payment_id=session_id,
            status="created",
            checkout_url=connect_url,
            payment_method="bank_transfer",
            provider_data={"session_id": session_id},
        )

    def get_payment_status(self, provider_payment_id) -> PaymentResult:
        """Get payment status from Fintecture."""
        token = self._get_access_token()
        resp = http_requests.get(
            f"{_API_BASE}/pis/v2/payments/{provider_payment_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        status_str = data.get("meta", {}).get("status", "")
        return PaymentResult(
            provider_payment_id=provider_payment_id,
            status=_STATUS_MAP.get(status_str, "pending"),
            payment_method="bank_transfer",
            provider_data=data,
        )

    def verify_webhook(self, headers, body) -> bool:
        """Verify Fintecture webhook signature."""
        if not self.webhook_secret:
            return False
        signature = headers.get("Signature", "")
        expected = hmac.new(
            self.webhook_secret.encode("utf-8"),
            body if isinstance(body, bytes) else body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def parse_webhook(self, headers, body) -> WebhookEvent:
        """Parse Fintecture webhook into normalized event."""
        payload = json.loads(body) if isinstance(body, bytes) else body

        meta = payload.get("meta", {})
        session_id = meta.get("session_id", "")
        status_str = meta.get("status", "")
        event_id = meta.get("event_id", session_id)

        if status_str == "payment_successful":
            norm_type = PaymentEvent.CONFIRMED
        elif status_str in ("payment_unsuccessful", "payment_error"):
            norm_type = PaymentEvent.FAILED
        else:
            norm_type = status_str

        return WebhookEvent(
            provider_event_id=event_id,
            event_type=norm_type,
            provider_payment_id=session_id,
            metadata={
                "payment_method": "bank_transfer",
                "status": status_str,
            },
            raw_data=payload,
        )
