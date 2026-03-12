"""FactPulse API client — handles communication with the FactPulse API via JWT."""

import base64
import dataclasses
import json
import logging
import threading
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class FactPulseError(Exception):
    """Raised when the FactPulse API returns an error."""

    def __init__(self, message, status_code=None, details=None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details or {}


class FactPulseUnavailableError(FactPulseError):
    """Raised when the FactPulse API is unreachable."""

    pass


@dataclasses.dataclass
class _TokenEntry:
    access: str
    refresh: str | None
    expires_at: float


# FactPulse API task statuses (returned by /processing/tasks/{id}/status)
_TASK_SUCCESS_STATUSES = frozenset({"completed", "SUCCESS"})
_TASK_FAILURE_STATUSES = frozenset({"failed", "FAILURE"})


class FactPulseClient:
    """HTTP client for the FactPulse API, authenticated via JWT.

    Supports per-client_uid token caching: account-level tokens (client_uid=None)
    and client-scoped tokens (client_uid="<uuid>") are cached separately.
    """

    TOKEN_ENDPOINT = "/api/token/"  # nosec B105 — URL path, not a password
    TOKEN_REFRESH_ENDPOINT = "/api/token/refresh/"  # nosec B105
    # Refresh the access token 60s before it expires
    TOKEN_REFRESH_MARGIN = 60

    def __init__(self):
        self.base_url = settings.FACTPULSE_API_URL.rstrip("/")
        self.email = getattr(settings, "FACTPULSE_EMAIL", "")
        self.password = getattr(settings, "FACTPULSE_PASSWORD", "")
        self.timeout = 30

        # Per-client_uid token cache: None = account-level, "uid" = client-level
        self._tokens: dict[str | None, _TokenEntry] = {}
        self._lock = threading.Lock()

    @property
    def is_configured(self):
        return bool(self.base_url and self.email and self.password)

    # ---- Auth ----

    def _obtain_tokens(self, client_uid=None):
        """Authenticate with email/password and obtain JWT pair."""
        url = f"{self.base_url}{self.TOKEN_ENDPOINT}"
        payload = {"username": self.email, "password": self.password}
        if client_uid:
            payload["client_uid"] = client_uid

        logger.debug(
            "[FactPulse] _obtain_tokens payload keys=%s, client_uid=%s",
            list(payload.keys()),
            client_uid,
        )

        try:
            response = requests.post(url, json=payload, timeout=self.timeout)
        except requests.ConnectionError:
            raise FactPulseUnavailableError(
                "Cannot connect to FactPulse API."
            ) from None
        except requests.Timeout:
            raise FactPulseUnavailableError(
                "FactPulse API request timed out."
            ) from None
        if response.status_code != 200:
            raise FactPulseError(
                "FactPulse authentication failed.",
                status_code=response.status_code,
            )

        data = response.json()
        lifetime = data.get("access_lifetime", 3600)
        self._tokens[client_uid] = _TokenEntry(
            access=data["access"],
            refresh=data.get("refresh"),
            expires_at=time.monotonic() + lifetime - self.TOKEN_REFRESH_MARGIN,
        )

    def _refresh_access_token(self, client_uid=None):
        """Use the refresh token to get a new access token."""
        entry = self._tokens.get(client_uid)
        if not entry or not entry.refresh:
            self._obtain_tokens(client_uid)
            return

        url = f"{self.base_url}{self.TOKEN_REFRESH_ENDPOINT}"
        try:
            response = requests.post(
                url, json={"refresh": entry.refresh}, timeout=self.timeout
            )
        except requests.RequestException:
            self._obtain_tokens(client_uid)
            return

        if response.status_code == 200:
            data = response.json()
            lifetime = data.get("access_lifetime", 3600)
            entry.access = data["access"]
            entry.expires_at = time.monotonic() + lifetime - self.TOKEN_REFRESH_MARGIN
        else:
            self._obtain_tokens(client_uid)

    def _ensure_token(self, client_uid=None):
        """Ensure we have a valid access token, refreshing if needed."""
        with self._lock:
            entry = self._tokens.get(client_uid)
            if entry and time.monotonic() < entry.expires_at:
                return
            if entry:
                self._refresh_access_token(client_uid)
            else:
                self._obtain_tokens(client_uid)

    def _headers(self, client_uid=None):
        self._ensure_token(client_uid)
        entry = self._tokens[client_uid]
        return {
            "Authorization": f"Bearer {entry.access}",
            "Accept": "application/json",
        }

    # ---- Requests ----

    def _request(self, method, url, client_uid=None, **kwargs):
        """Make an authenticated request, retrying once on 401."""
        kwargs.setdefault("timeout", self.timeout)
        headers = self._headers(client_uid)
        try:
            response = requests.request(method, url, headers=headers, **kwargs)
        except requests.ConnectionError:
            raise FactPulseUnavailableError(
                "Cannot connect to FactPulse API."
            ) from None
        except requests.Timeout:
            raise FactPulseUnavailableError(
                "FactPulse API request timed out."
            ) from None

        if response.status_code not in (200, 201):
            logger.debug("[FactPulse] %s %s -> %s", method, url, response.status_code)
            logger.debug("[FactPulse] %s", response.content)

        if response.status_code == 401:
            # Token may have been invalidated server-side, retry once
            with self._lock:
                self._obtain_tokens(client_uid)
            headers = self._headers(client_uid)
            try:
                response = requests.request(method, url, headers=headers, **kwargs)
            except requests.RequestException:
                raise FactPulseUnavailableError(
                    "Cannot connect to FactPulse API."
                ) from None

        return response

    def _handle_error(self, response):
        try:
            error_data = response.json()
        except ValueError:
            error_data = {"message": response.text}

        # AFNOR / FactPulse errors: {"errorCode": "...", "errorMessage": "..."}
        if error_data.get("errorMessage"):
            message = error_data["errorMessage"]
        elif error_data.get("errorCode"):
            message = f"{error_data['errorCode']} ({response.status_code})"
        else:
            # Legacy/other format: {"detail": "..."} or {"detail": {"error": "..."}}
            detail = error_data.get("detail")
            if isinstance(detail, dict):
                message = detail.get("error", str(detail))
            elif isinstance(detail, str):
                message = detail
            else:
                message = error_data.get(
                    "message", f"FactPulse API error ({response.status_code})"
                )

        raise FactPulseError(
            message=message,
            status_code=response.status_code,
            details=error_data,
        )

    # ---- Client Management (account-level) ----

    def create_client(self, name, siret=None, description=None):
        """POST /api/v1/clients — create a new FactPulse client."""
        if not self.is_configured:
            raise FactPulseUnavailableError("FactPulse API is not configured.")

        url = f"{self.base_url}/api/v1/clients"
        payload = {"name": name}
        if siret:
            payload["siret"] = siret
        if description:
            payload["description"] = description

        response = self._request("POST", url, json=payload)
        if response.status_code in (200, 201):
            return response.json()
        self._handle_error(response)

    def list_clients(self):
        """GET /api/v1/clients — list FactPulse clients."""
        if not self.is_configured:
            raise FactPulseUnavailableError("FactPulse API is not configured.")

        url = f"{self.base_url}/api/v1/clients"
        response = self._request("GET", url)
        if response.status_code == 200:
            return response.json()
        self._handle_error(response)

    # ---- PDP Config (client-level) ----

    def get_pdp_config(self, client_uid):
        """GET /api/v1/clients/{uid}/pdp-config — get PDP configuration status."""
        if not self.is_configured:
            raise FactPulseUnavailableError("FactPulse API is not configured.")

        url = f"{self.base_url}/api/v1/clients/{client_uid}/pdp-config"
        response = self._request("GET", url, client_uid=str(client_uid))
        if response.status_code == 200:
            return response.json()
        self._handle_error(response)

    def delete_pdp_config(self, client_uid):
        """DELETE /api/v1/clients/{uid}/pdp-config — remove existing PDP config."""
        if not self.is_configured:
            raise FactPulseUnavailableError("FactPulse API is not configured.")

        url = f"{self.base_url}/api/v1/clients/{client_uid}/pdp-config"
        response = self._request("DELETE", url, client_uid=str(client_uid))
        if response.status_code in (200, 204):
            return True
        self._handle_error(response)

    def push_pdp_config(self, client_uid, config):
        """PUT /api/v1/clients/{uid}/pdp-config — push PDP credentials."""
        if not self.is_configured:
            raise FactPulseUnavailableError("FactPulse API is not configured.")

        url = f"{self.base_url}/api/v1/clients/{client_uid}/pdp-config"
        response = self._request("PUT", url, client_uid=str(client_uid), json=config)
        if response.status_code == 200:
            return response.json()
        self._handle_error(response)

    # ---- Processing API methods ----

    def generate_invoice(self, invoice_data, source_pdf=None, client_uid=None):
        """Call POST /api/v1/processing/generate-invoice and poll for result.

        FactPulse returns 202 with a taskId for async processing.
        We poll GET /processing/tasks/{taskId}/status until completion,
        then return the Factur-X PDF bytes.
        """
        if not self.is_configured:
            raise FactPulseUnavailableError(
                "FactPulse API is not configured (FACTPULSE_API_URL or credentials missing)."
            )

        url = f"{self.base_url}/api/v1/processing/generate-invoice"

        files = {}
        if source_pdf:
            files["source_pdf"] = ("invoice.pdf", source_pdf, "application/pdf")

        response = self._request(
            "POST",
            url,
            client_uid=client_uid,
            data={
                "invoice_data": json.dumps(invoice_data),
                "profile": "EN16931",
            },
            files=files or None,
        )

        if response.status_code == 200:
            return response.content

        if response.status_code == 202:
            task_id = response.json().get("taskId")
            if not task_id:
                raise FactPulseError("202 response but no taskId returned.")
            return self._poll_task_result(task_id, client_uid=client_uid)

        self._handle_error(response)

    def _poll_task_result(self, task_id, client_uid=None, max_attempts=30, interval=2):
        """Poll GET /processing/tasks/{taskId}/status until completed."""
        url = f"{self.base_url}/api/v1/processing/tasks/{task_id}/status"

        for attempt in range(max_attempts):
            response = self._request("GET", url, client_uid=client_uid)
            if response.status_code != 200:
                self._handle_error(response)

            data = response.json()
            status = data.get("status", "")

            if status in _TASK_SUCCESS_STATUSES:
                result = data.get("result", {})

                # Task completed but with a business error inside
                if result.get("errorCode") or result.get("errorMessage"):
                    raise FactPulseError(
                        result.get("errorMessage", "Task failed"),
                        details=result,
                    )

                pdf_b64 = (
                    result.get("content_b64")
                    or result.get("pdf_base64")
                    or result.get("pdf")
                )
                if pdf_b64:
                    return base64.b64decode(pdf_b64)
                raise FactPulseError(
                    f"Task completed but no PDF in response. Keys: {list(result.keys())}"
                )

            if status in _TASK_FAILURE_STATUSES:
                result = data.get("result", {})
                raise FactPulseError(
                    result.get("errorMessage", "Task failed"),
                    details=result,
                )

            logger.debug(
                "Task %s status: %s (attempt %d)", task_id, status, attempt + 1
            )
            time.sleep(interval)

        raise FactPulseError(
            f"Task {task_id} did not complete after {max_attempts} attempts."
        )

    # ---- AFNOR Directory Service (XP Z12-013 Annexe B) ----

    def get_directory_siren(self, siren, client_uid=None):
        """GET /afnor/directory/v1/siren/code-insee:{siren} — lookup company in AFNOR directory.

        Args:
            siren: 9-digit SIREN number.
            client_uid: organisation's FactPulse client UID.

        Returns:
            Dict with company directory data.
        """
        if not self.is_configured:
            raise FactPulseUnavailableError("FactPulse API is not configured.")

        url = f"{self.base_url}/api/v1/afnor/directory/v1/siren/code-insee:{siren}"
        response = self._request("GET", url, client_uid=client_uid)

        if response.status_code == 200:
            return response.json()

        self._handle_error(response)

    def search_directory_lines(self, siren, client_uid=None):
        """POST /afnor/directory/v1/directory-line/search — find addressing lines by SIREN.

        The directory line is the location at which the recipient wishes to
        receive invoices.  Returns active lines only (per AFNOR spec).

        Args:
            siren: 9-digit SIREN number.
            client_uid: organisation's FactPulse client UID.

        Returns:
            Dict with search results containing directory lines
            (addressingIdentifier, siren, siret, plateform, …).
        """
        if not self.is_configured:
            raise FactPulseUnavailableError("FactPulse API is not configured.")

        url = f"{self.base_url}/api/v1/afnor/directory/v1/directory-line/search"
        payload = {
            "filters": {
                "siren": {"op": "contains", "value": siren},
            },
            "sorting": [{"field": "addressingIdentifier", "sortingOrder": "ascending"}],
            "fields": [
                "addressingIdentifier",
                "siren",
                "siret",
                "addressingSuffix",
                "idInstance",
            ],
            "limit": 50,
            "ignore": 0,
        }
        logger.info(
            "[FactPulse] directory-line/search payload: %s", json.dumps(payload)
        )
        response = self._request("POST", url, client_uid=client_uid, json=payload)
        logger.info(
            "[FactPulse] directory-line/search response: %s %s",
            response.status_code,
            response.text[:500] if response.text else "(empty)",
        )

        if response.status_code == 200:
            return response.json()

        self._handle_error(response)

    # ---- AFNOR Flow Service (XP Z12-013) ----

    def get_flow_status(self, flow_id, client_uid=None):
        """GET /afnor/flow/v1/flows/{flowId} — retrieve flow metadata & acknowledgement."""
        if not self.is_configured:
            raise FactPulseUnavailableError("FactPulse API is not configured.")

        url = f"{self.base_url}/api/v1/afnor/flow/v1/flows/{flow_id}"
        response = self._request("GET", url, client_uid=client_uid)

        if response.status_code == 200:
            return response.json()

        self._handle_error(response)

    def submit_flow(
        self, flow_info, file_bytes, filename="facturx.pdf", client_uid=None
    ):
        """POST /afnor/flow/v1/flows — submit an invoicing flow (AFNOR XP Z12-013).

        Args:
            flow_info: dict with flowSyntax (required), trackingId, name,
                       flowProfile, processingRule, sha256.
            file_bytes: PDF/A-3 (Factur-X) or XML bytes.
            filename: name for the uploaded file.
            client_uid: organisation's FactPulse client UID.

        Returns:
            FullFlowInfo dict (flowId, submittedAt, flowSyntax, …).
        """
        if not self.is_configured:
            raise FactPulseUnavailableError("FactPulse API is not configured.")

        url = f"{self.base_url}/api/v1/afnor/flow/v1/flows"

        files = {
            "flowInfo": (None, json.dumps(flow_info), "application/json"),
            "file": (filename, file_bytes, "application/pdf"),
        }

        response = self._request(
            "POST",
            url,
            client_uid=client_uid,
            files=files,
        )

        if response.status_code in (200, 201, 202):
            return response.json()

        self._handle_error(response)

    # ---- E-Reporting (DGFiP v3.1, XP Z12-014) ----

    def submit_ereporting(self, payload, client_uid=None):
        """POST /api/v1/ereporting/submit — generate and submit e-reporting to PA.

        Args:
            payload: dict with fluxType, sender, period, invoices/transactions, etc.
            client_uid: organisation's FactPulse client UID.

        Returns:
            dict with submission result (flowId, status, etc.).
        """
        if not self.is_configured:
            raise FactPulseUnavailableError("FactPulse API is not configured.")

        url = f"{self.base_url}/api/v1/ereporting/submit"
        response = self._request("POST", url, client_uid=client_uid, json=payload)

        if response.status_code in (200, 201, 202):
            return response.json()

        self._handle_error(response)

    def validate_ereporting(self, payload, client_uid=None):
        """POST /api/v1/ereporting/validate — validate e-reporting data.

        Returns validation result (errors, warnings).
        """
        if not self.is_configured:
            raise FactPulseUnavailableError("FactPulse API is not configured.")

        url = f"{self.base_url}/api/v1/ereporting/validate"
        response = self._request("POST", url, client_uid=client_uid, json=payload)

        if response.status_code == 200:
            return response.json()

        self._handle_error(response)

    # ---- CDAR (XP Z12-014) ----

    def submit_paid_status(self, data, client_uid=None):
        """POST /api/v1/cdar/encaissee — submit paid status (212) to the PA."""
        if not self.is_configured:
            raise FactPulseUnavailableError("FactPulse API is not configured.")

        url = f"{self.base_url}/api/v1/cdar/encaissee"
        response = self._request("POST", url, client_uid=client_uid, json=data)

        if response.status_code in (200, 201, 202):
            return response.json()

        self._handle_error(response)

    def get_cdar_lifecycle(self, days=7, invoice_id=None, client_uid=None):
        """GET /api/v1/cdar/lifecycle — retrieve CDAR lifecycle events.

        Args:
            days: lookback window in days (default 7).
            invoice_id: optional invoice reference filter (e.g. "FA-2026-001").
            client_uid: organisation's FactPulse client UID.

        Returns:
            {invoices: [{sellerId, invoiceId, events: [...], totalEvents}],
             totalInvoices, cutoffDays}
        """
        if not self.is_configured:
            raise FactPulseUnavailableError("FactPulse API is not configured.")

        url = f"{self.base_url}/api/v1/cdar/lifecycle"
        params = {"days": days}
        if invoice_id:
            params["invoiceId"] = invoice_id
        response = self._request("GET", url, client_uid=client_uid, params=params)

        if response.status_code == 200:
            return response.json()

        self._handle_error(response)


# Singleton client
client = FactPulseClient()
