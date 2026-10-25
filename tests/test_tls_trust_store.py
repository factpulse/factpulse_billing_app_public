"""Magasin de certificats utilisé pour les appels HTTPS sortants.

`requests` (API FactPulse, SIRENE, Fintecture, GoCardless, webhooks) vérifie
TLS avec le bundle `certifi`. Une mise à jour de certifi peut retirer des
racines : ces tests vérifient hors ligne que les racines des services
appelés restent présentes, et que les racines distrustées ont disparu.

Stripe n'est pas concerné : le SDK embarque son propre bundle.
"""

import ssl

import certifi
import pytest
import requests.certs


def _root_common_names():
    ctx = ssl.create_default_context(cafile=certifi.where())
    names = set()
    for cert in ctx.get_ca_certs():
        subject = dict(rdn[0] for rdn in cert["subject"])
        names.add(subject.get("commonName") or subject.get("organizationalUnitName"))
    return names


def test_requests_uses_certifi_bundle():
    assert requests.certs.where() == certifi.where()


@pytest.mark.parametrize(
    "root, services",
    [
        # Let's Encrypt : factpulse.fr, *.api.gouv.fr, api.superpdp.tech
        ("ISRG Root X1", "Let's Encrypt (RSA)"),
        ("ISRG Root X2", "Let's Encrypt (ECDSA)"),
        # Google Trust Services : api.gocardless.com, api.fintecture.com
        ("GTS Root R1", "Fintecture"),
        ("GTS Root R4", "GoCardless"),
    ],
)
def test_bundle_trusts_roots_of_called_services(root, services):
    assert root in _root_common_names(), f"{root} absent ({services})"


@pytest.mark.parametrize(
    "root",
    [
        # GHSA-6q48-6c7w-9gw2 : clés d'intermédiaires compromises
        "emSign Root CA - C1",
        "emSign ECC Root CA - C3",
        "emSign ECC Root CA - G3",
    ],
)
def test_bundle_excludes_distrusted_roots(root):
    assert root not in _root_common_names()
