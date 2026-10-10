"""Tests for the copy_media_from_s3 management command."""

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import CommandError, call_command

from apps.billing.factories import InvoiceFactory

KEY = "invoices/2026/10/facture.pdf"


def _fake_s3(objects):
    """Fake boto3 S3 client serving {key: bytes}."""
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": k, "Size": len(v)} for k, v in objects.items()]}
    ]

    def download_file(bucket, key, target):
        with open(target, "wb") as f:
            f.write(objects[key])

    client.download_file.side_effect = download_file
    return client


def _run(**kwargs):
    out = StringIO()
    call_command("copy_media_from_s3", stdout=out, **kwargs)
    return out.getvalue()


@pytest.mark.django_db
def test_copies_objects_and_checks_references(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    InvoiceFactory(pdf_file=KEY)
    client = _fake_s3({KEY: b"%PDF-1.7"})

    with patch("boto3.client", return_value=client):
        out = _run()

    assert (tmp_path / KEY).read_bytes() == b"%PDF-1.7"
    assert "All referenced files are present." in out

    # Second run: same size already present → skipped
    with patch("boto3.client", return_value=client):
        out = _run()
    assert "0 copied, 1 already present." in out


@pytest.mark.django_db
def test_dry_run_writes_nothing(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    with patch("boto3.client", return_value=_fake_s3({KEY: b"x"})):
        out = _run(dry_run=True)

    assert "would copy" in out
    assert not (tmp_path / KEY).exists()


@pytest.mark.django_db
def test_reports_missing_referenced_file(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    InvoiceFactory(pdf_file="invoices/absent.pdf")

    with patch("boto3.client", return_value=_fake_s3({})):
        with pytest.raises(CommandError):
            _run()


@pytest.mark.django_db
def test_rejects_key_outside_media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    with patch("boto3.client", return_value=_fake_s3({"../evil": b"x"})):
        with pytest.raises(CommandError):
            _run()
