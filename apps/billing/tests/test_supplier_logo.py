"""Logo fournisseur : seul chemin de l'app où Pillow décode des images.

Pillow intervient à deux endroits :
- à l'upload via l'API, `ImageField` (Django/DRF) ouvre et vérifie le fichier ;
- à la génération du PDF source, WeasyPrint décode le logo (data URI) avec
  Pillow puis le réencode dans le PDF.

Ces tests figent le comportement attendu pour sécuriser les montées de
version de Pillow / WeasyPrint.
"""

import io
import struct
import time
import zlib

import pytest
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from PIL import Image

from apps.core.models import OrganizationMembership
from apps.factpulse.tasks import _generate_source_pdf


@pytest.fixture(autouse=True)
def _tmp_media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def _image_bytes(mode, fmt, color, size=(40, 20), **save_kwargs):
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, fmt, **save_kwargs)
    return buf.getvalue()


def _png_chunk(chunk_type, data):
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data))
    )


def _decompression_bomb_png(width=30000, height=30000):
    """PNG de quelques octets dont l'en-tête annonce 900 Mpx."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(b"\x00" * 10))
        + _png_chunk(b"IEND", b"")
    )


def _oversized_png():
    """PNG valide de plus de 2 Mo (bruit incompressible)."""
    noise = Image.frombytes("RGB", (1024, 1024), bytes(range(256)) * 12288)
    buf = io.BytesIO()
    noise.effect_spread(50).save(buf, "PNG", compress_level=0)
    assert buf.tell() > 2 * 1024 * 1024
    return buf.getvalue()


# Logos qu'un utilisateur peut réalistement envoyer (export Canva, Photoshop
# CMJN, PNG indexé avec transparence, niveaux de gris, 16 bits…).
LOGOS = {
    "rgb.png": _image_bytes("RGB", "PNG", (200, 30, 30)),
    "rgba.png": _image_bytes("RGBA", "PNG", (200, 30, 30, 128)),
    "palette.png": _image_bytes("P", "PNG", 3),
    "palette_transparency.png": _image_bytes("P", "PNG", 3, transparency=3),
    "grayscale_alpha.png": _image_bytes("LA", "PNG", (128, 200)),
    "bilevel.png": _image_bytes("1", "PNG", 1),
    "16bit.png": _image_bytes("I;16", "PNG", 30000),
    "rgb.jpg": _image_bytes("RGB", "JPEG", (200, 30, 30)),
    "cmyk.jpg": _image_bytes("CMYK", "JPEG", (0, 255, 255, 0)),
    "grayscale.jpeg": _image_bytes("L", "JPEG", 128),
    "progressive.jpg": _image_bytes("RGB", "JPEG", (200, 30, 30), progressive=True),
    "rgba.webp": _image_bytes("RGBA", "WEBP", (200, 30, 30, 128)),
}

SVG_LOGO = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">'
    b'<rect width="40" height="20" fill="red"/></svg>'
)


# --- Upload API (Pillow via ImageField) ---


def _error_fields(resp):
    return {d["field"] for d in resp.json()["error"]["details"]}


@pytest.mark.django_db
class TestLogoUploadApi:
    def _patch_logo(self, client, supplier, name, content):
        return client.patch(
            f"/api/v1/suppliers/{supplier.uuid}/",
            {"logo": SimpleUploadedFile(name, content)},
            format="multipart",
        )

    @pytest.mark.parametrize("name", list(LOGOS))
    def test_accepts_valid_image(self, auth_api_client, supplier, name):
        resp = self._patch_logo(auth_api_client, supplier, name, LOGOS[name])

        assert resp.status_code == 200, resp.json()
        supplier.refresh_from_db()
        assert supplier.logo.name.startswith("suppliers/logos/")
        with supplier.logo.open("rb") as f:
            assert f.read() == LOGOS[name]

    def test_rejects_non_image_with_image_extension(self, auth_api_client, supplier):
        resp = self._patch_logo(auth_api_client, supplier, "logo.png", b"not an image")

        assert resp.status_code == 400
        assert "logo" in _error_fields(resp)
        supplier.refresh_from_db()
        assert not supplier.logo

    def test_rejects_truncated_image(self, auth_api_client, supplier):
        truncated = LOGOS["rgb.png"][:60]

        resp = self._patch_logo(auth_api_client, supplier, "logo.png", truncated)

        assert resp.status_code == 400
        assert "logo" in _error_fields(resp)

    def test_rejects_disallowed_format(self, auth_api_client, supplier):
        gif = _image_bytes("P", "GIF", 3)

        resp = self._patch_logo(auth_api_client, supplier, "logo.gif", gif)

        assert resp.status_code == 400
        assert "logo" in _error_fields(resp)

    def test_rejects_oversized_image(self, auth_api_client, supplier):
        resp = self._patch_logo(auth_api_client, supplier, "big.png", _oversized_png())

        assert resp.status_code == 400
        assert "logo" in _error_fields(resp)

    def test_decompression_bomb_does_not_hang(self, auth_api_client, supplier):
        start = time.monotonic()

        resp = self._patch_logo(
            auth_api_client, supplier, "bomb.png", _decompression_bomb_png()
        )

        assert time.monotonic() - start < 5
        assert resp.status_code in (200, 400)


# --- Upload UI ---


@pytest.mark.django_db
class TestLogoUploadUi:
    @pytest.fixture
    def owner_client(self, org):
        user = User.objects.create_user(
            username="logo@test.local",
            email="logo@test.local",
            password="testpass123",  # nosec B106
        )
        OrganizationMembership.objects.create(user=user, organization=org, role="owner")
        client = Client()
        client.login(username="logo@test.local", password="testpass123")
        return client

    def test_upload_then_clear(self, owner_client, supplier):
        url = f"/suppliers/{supplier.uuid}/settings/"

        resp = owner_client.post(
            url, {"logo": SimpleUploadedFile("logo.png", LOGOS["rgba.png"])}
        )

        assert resp.status_code == 302
        supplier.refresh_from_db()
        with supplier.logo.open("rb") as f:
            assert f.read() == LOGOS["rgba.png"]

        resp = owner_client.post(url, {"logo_clear": "on"})

        assert resp.status_code == 302
        supplier.refresh_from_db()
        assert not supplier.logo

    @pytest.mark.parametrize(
        "name, content",
        [
            pytest.param("logo.png", b"not an image", id="not-an-image"),
            pytest.param("logo.png", LOGOS["rgb.png"][:60], id="truncated"),
            pytest.param("logo.gif", _image_bytes("P", "GIF", 3), id="gif"),
            pytest.param("logo.tiff", _image_bytes("RGB", "TIFF", 0), id="tiff"),
            pytest.param("big.png", _oversized_png(), id="oversized"),
            pytest.param("bomb.png", _decompression_bomb_png(), id="bomb"),
        ],
    )
    def test_rejects_invalid_logo(self, owner_client, supplier, name, content):
        resp = owner_client.post(
            f"/suppliers/{supplier.uuid}/settings/",
            {
                "logo": SimpleUploadedFile(name, content),
                "pdf_legal_mentions": "Ne doit pas être enregistré",
            },
        )

        assert resp.status_code == 400
        assert resp.context["error"]
        supplier.refresh_from_db()
        assert not supplier.logo
        assert supplier.pdf_legal_mentions != "Ne doit pas être enregistré"

    def test_rejected_logo_keeps_existing_one(self, owner_client, supplier):
        supplier.logo.save("logo.png", ContentFile(LOGOS["rgb.png"]), save=True)
        previous = supplier.logo.name

        resp = owner_client.post(
            f"/suppliers/{supplier.uuid}/settings/",
            {"logo": SimpleUploadedFile("logo.png", b"not an image")},
        )

        assert resp.status_code == 400
        supplier.refresh_from_db()
        assert supplier.logo.name == previous


# --- Rendu PDF (Pillow via WeasyPrint) ---


def _image_count(pdf_bytes):
    return pdf_bytes.count(b"/Image")


@pytest.fixture
def invoice_pdf_baseline(draft_invoice):
    """Nombre d'images présentes dans le PDF sans logo."""
    return _image_count(_generate_source_pdf(draft_invoice))


@pytest.mark.django_db
class TestLogoInSourcePdf:
    def _set_logo(self, supplier, name, content):
        supplier.logo.save(name, ContentFile(content), save=True)

    @pytest.mark.parametrize("name", list(LOGOS))
    def test_raster_logo_is_embedded(
        self, draft_invoice, invoice_pdf_baseline, name, caplog
    ):
        self._set_logo(draft_invoice.supplier, name, LOGOS[name])

        with caplog.at_level("ERROR", logger="weasyprint"):
            pdf = _generate_source_pdf(draft_invoice)

        assert pdf.startswith(b"%PDF-")
        assert _image_count(pdf) > invoice_pdf_baseline
        assert not [r for r in caplog.records if "image" in r.getMessage().lower()]

    def test_svg_logo_is_rendered(self, draft_invoice, caplog):
        self._set_logo(draft_invoice.supplier, "logo.svg", SVG_LOGO)

        with caplog.at_level("ERROR", logger="weasyprint"):
            pdf = _generate_source_pdf(draft_invoice)

        assert pdf.startswith(b"%PDF-")
        assert not [r for r in caplog.records if "image" in r.getMessage().lower()]

    @pytest.mark.parametrize(
        "content",
        [
            pytest.param(_decompression_bomb_png(), id="decompression-bomb"),
            pytest.param(LOGOS["rgb.png"][:60], id="truncated"),
            pytest.param(b"\x89PNG\r\n\x1a\nnope", id="garbage"),
        ],
    )
    def test_hostile_logo_does_not_break_pdf(
        self, draft_invoice, invoice_pdf_baseline, content
    ):
        # L'UI enregistre le logo sans le valider : le rendu doit tenir quoi
        # qu'il arrive (pas de crash, pas d'explosion mémoire/CPU).
        self._set_logo(draft_invoice.supplier, "logo.png", content)
        start = time.monotonic()

        pdf = _generate_source_pdf(draft_invoice)

        assert time.monotonic() - start < 10
        assert pdf.startswith(b"%PDF-")

    def test_decompression_bomb_is_not_embedded(
        self, draft_invoice, invoice_pdf_baseline
    ):
        self._set_logo(draft_invoice.supplier, "logo.png", _decompression_bomb_png())

        pdf = _generate_source_pdf(draft_invoice)

        assert _image_count(pdf) == invoice_pdf_baseline
