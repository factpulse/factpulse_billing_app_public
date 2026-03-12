"""Tests for webhooks app — services and viewset."""

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIClient

from apps.core.models import Organization
from apps.webhooks.models import WebhookDelivery, WebhookEndpoint
from apps.webhooks.services import deliver_webhook, emit_webhook

# --- Helpers ---


def _create_endpoint(org, **kwargs):
    defaults = {
        "organization": org,
        "url": "https://example.com/webhook",
        "secret": "test-secret",
        "events": [],
        "is_active": True,
    }
    defaults.update(kwargs)
    return WebhookEndpoint.objects.create(**defaults)


# --- emit_webhook ---


@pytest.mark.django_db
class TestEmitWebhook:
    @patch("apps.webhooks.tasks.send_webhook")
    def test_dispatches_to_active_endpoints(self, mock_task, org):
        _create_endpoint(org)
        _create_endpoint(org, url="https://other.com/hook")
        _create_endpoint(org, is_active=False)

        emit_webhook(org, "invoice.validated", {"uuid": "123"})

        assert mock_task.delay.call_count == 2

    @patch("apps.webhooks.tasks.send_webhook")
    def test_filters_by_events(self, mock_task, org):
        _create_endpoint(org, events=["invoice.validated"])
        _create_endpoint(org, events=["invoice.transmitted"])

        emit_webhook(org, "invoice.validated", {"uuid": "123"})

        assert mock_task.delay.call_count == 1

    @patch("apps.webhooks.tasks.send_webhook")
    def test_empty_events_receives_all(self, mock_task, org):
        _create_endpoint(org, events=[])

        emit_webhook(org, "invoice.transmitted", {"uuid": "123"})

        assert mock_task.delay.call_count == 1

    @patch("apps.webhooks.tasks.send_webhook")
    def test_no_endpoints_no_dispatch(self, mock_task, org):
        emit_webhook(org, "invoice.validated", {"uuid": "123"})

        assert mock_task.delay.call_count == 0


# --- deliver_webhook ---


@pytest.mark.django_db
class TestDeliverWebhook:
    @patch("requests.post")
    def test_hmac_signature_correct(self, mock_post, org):
        endpoint = _create_endpoint(org, secret="my-secret-key")
        payload = {"event": "invoice.validated", "data": {"uuid": "123"}}

        mock_post.return_value = MagicMock(status_code=200, text="OK")
        deliver_webhook(endpoint.pk, payload)

        call_args = mock_post.call_args
        actual_signature = call_args.kwargs["headers"]["X-Webhook-Signature"]

        # Verify independently
        payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        expected_signature = hmac.new(
            b"my-secret-key", payload_bytes, hashlib.sha256
        ).hexdigest()
        assert actual_signature == expected_signature

    @patch("requests.post")
    def test_success_200(self, mock_post, org):
        endpoint = _create_endpoint(org)
        payload = {"event": "test", "data": {}}

        mock_post.return_value = MagicMock(status_code=200, text="OK")
        deliver_webhook(endpoint.pk, payload)

        delivery = WebhookDelivery.objects.get(endpoint=endpoint)
        assert delivery.success is True
        assert delivery.http_status == 200

    @patch("apps.webhooks.tasks.send_webhook")
    @patch("requests.post")
    def test_failure_schedules_retry(self, mock_post, mock_task, org):
        endpoint = _create_endpoint(org)
        payload = {"event": "test", "data": {}}

        mock_post.return_value = MagicMock(status_code=500, text="Error")
        deliver_webhook(endpoint.pk, payload, attempt=1)

        delivery = WebhookDelivery.objects.get(endpoint=endpoint)
        assert delivery.success is False
        mock_task.apply_async.assert_called_once()

    @patch("requests.post")
    def test_third_failure_disables_endpoint(self, mock_post, org):
        endpoint = _create_endpoint(org)
        payload = {"event": "test", "data": {}}

        mock_post.return_value = MagicMock(status_code=500, text="Error")
        deliver_webhook(endpoint.pk, payload, attempt=3)

        endpoint.refresh_from_db()
        assert endpoint.is_active is False

    @patch("apps.webhooks.tasks.send_webhook")
    @patch("requests.post")
    def test_timeout_handled(self, mock_post, mock_task, org):
        import requests as real_requests

        endpoint = _create_endpoint(org)
        payload = {"event": "test", "data": {}}

        mock_post.side_effect = real_requests.ConnectionError("timeout")
        deliver_webhook(endpoint.pk, payload, attempt=1)

        delivery = WebhookDelivery.objects.get(endpoint=endpoint)
        assert delivery.success is False
        assert delivery.http_status is None

    def test_nonexistent_endpoint_silent(self, org):
        # Should not raise
        deliver_webhook(99999, {"event": "test"})
        assert WebhookDelivery.objects.count() == 0


# --- WebhookEndpointViewSet ---


@pytest.mark.django_db
class TestWebhookEndpointViewSet:
    url = "/api/v1/webhooks/"

    def _auth_client(self, user):
        from rest_framework_simplejwt.tokens import RefreshToken

        client = APIClient()
        refresh = RefreshToken.for_user(user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        return client

    def test_create(self, auth_api_client, org):
        resp = auth_api_client.post(
            self.url,
            {"url": "https://example.com/hook", "secret": "s3cr3t", "events": []},
            format="json",
        )
        assert resp.status_code == 201
        assert "secret" not in resp.json()  # write-only

    def test_list(self, auth_api_client, org):
        _create_endpoint(org)
        resp = auth_api_client.get(self.url)
        assert resp.status_code == 200
        data = resp.json()
        results = data.get("results", data)
        assert len(results) == 1
        assert "secret" not in results[0]  # write-only

    def test_update(self, auth_api_client, org):
        endpoint = _create_endpoint(org)
        resp = auth_api_client.patch(
            f"{self.url}{endpoint.uuid}/",
            {"url": "https://new-url.com/hook"},
            format="json",
        )
        assert resp.status_code == 200
        endpoint.refresh_from_db()
        assert endpoint.url == "https://new-url.com/hook"

    def test_delete(self, auth_api_client, org):
        endpoint = _create_endpoint(org)
        resp = auth_api_client.delete(f"{self.url}{endpoint.uuid}/")
        assert resp.status_code == 204

    def test_org_isolation(self, auth_api_client, org):
        other_org = Organization.objects.create(name="Other", slug="other-wh")
        _create_endpoint(other_org)
        resp = auth_api_client.get(self.url)
        data = resp.json()
        results = data.get("results", data)
        assert len(results) == 0

    def test_deliveries_action(self, auth_api_client, org):
        endpoint = _create_endpoint(org)
        WebhookDelivery.objects.create(
            endpoint=endpoint, event="test", payload={}, success=True
        )
        resp = auth_api_client.get(f"{self.url}{endpoint.uuid}/deliveries/")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_member_cannot_access(self, org, member_user):
        client = self._auth_client(member_user)
        resp = client.get(self.url)
        assert resp.status_code == 403
