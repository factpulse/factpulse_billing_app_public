"""Tests for the SIRENE API client (sirene_client.py)."""

from unittest.mock import MagicMock, patch

import pytest

from apps.billing.services.sirene_client import (
    SireneError,
    SireneNotFoundError,
    _build_address_line,
    _compute_vat_number,
    _normalize_siren_siret,
    lookup,
)

# --- _normalize_siren_siret ---


class TestNormalizeQuery:
    def test_valid_siren(self):
        assert _normalize_siren_siret("123456789") == "123456789"

    def test_valid_siret(self):
        assert _normalize_siren_siret("12345678901234") == "12345678901234"

    def test_strips_spaces(self):
        assert _normalize_siren_siret("123 456 789") == "123456789"

    def test_strips_multiple_spaces(self):
        assert _normalize_siren_siret(" 123 456 789 012 34 ") == "12345678901234"

    def test_invalid_contains_letters(self):
        with pytest.raises(SireneError, match="SIREN.*SIRET"):
            _normalize_siren_siret("12345678A")

    def test_invalid_too_short(self):
        with pytest.raises(SireneError, match="SIREN.*SIRET"):
            _normalize_siren_siret("12345678")

    def test_invalid_too_long(self):
        with pytest.raises(SireneError, match="SIREN.*SIRET"):
            _normalize_siren_siret("123456789012345")

    def test_invalid_ten_digits(self):
        with pytest.raises(SireneError):
            _normalize_siren_siret("1234567890")

    def test_invalid_empty(self):
        with pytest.raises(SireneError):
            _normalize_siren_siret("")


# --- _compute_vat_number ---


class TestComputeVatNumber:
    def test_known_siren(self):
        # SIREN 443061841 (known: FR40443061841)
        result = _compute_vat_number("443061841")
        assert result.startswith("FR")
        assert result.endswith("443061841")
        assert len(result) == 13  # FR + 2-digit key + 9-digit SIREN

    def test_format(self):
        result = _compute_vat_number("000000000")
        assert result.startswith("FR")
        assert len(result) == 13

    def test_key_zero_padded(self):
        # When key < 10, it should be zero-padded
        result = _compute_vat_number("000000003")
        key = (12 + 3 * (3 % 97)) % 97
        assert result == f"FR{key:02d}000000003"


# --- _build_address_line ---


class TestBuildAddressLine:
    def test_full_address(self):
        siege = {
            "numero_voie": "42",
            "indice_repetition_voie": "BIS",
            "type_voie": "RUE",
            "libelle_voie": "DE LA PAIX",
        }
        assert _build_address_line(siege) == "42 BIS RUE DE LA PAIX"

    def test_partial_address_no_numero(self):
        siege = {
            "numero_voie": None,
            "type_voie": "AVENUE",
            "libelle_voie": "DES CHAMPS ELYSEES",
        }
        assert _build_address_line(siege) == "AVENUE DES CHAMPS ELYSEES"

    def test_partial_address_no_indice(self):
        siege = {
            "numero_voie": "10",
            "type_voie": "BOULEVARD",
            "libelle_voie": "HAUSSMANN",
        }
        assert _build_address_line(siege) == "10 BOULEVARD HAUSSMANN"

    def test_empty_dict(self):
        assert _build_address_line({}) == ""

    def test_all_none_values(self):
        siege = {
            "numero_voie": None,
            "indice_repetition_voie": None,
            "type_voie": None,
            "libelle_voie": None,
        }
        assert _build_address_line(siege) == ""

    def test_only_libelle(self):
        siege = {"libelle_voie": "LIEU DIT LE MOULIN"}
        assert _build_address_line(siege) == "LIEU DIT LE MOULIN"


# --- lookup ---


@patch("apps.billing.services.sirene_client.requests")
class TestLookup:
    def _mock_response(self, mock_requests, json_data, status_code=200):
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.json.return_value = json_data
        mock_resp.raise_for_status.return_value = None
        mock_requests.get.return_value = mock_resp
        return mock_resp

    def test_success_siren(self, mock_requests):
        api_data = {
            "results": [
                {
                    "siren": "443061841",
                    "nom_complet": "GOOGLE FRANCE",
                    "etat_administratif": "A",
                    "siege": {
                        "siret": "44306184100047",
                        "numero_voie": "8",
                        "type_voie": "RUE",
                        "libelle_voie": "DE LONDRES",
                        "code_postal": "75009",
                        "libelle_commune": "PARIS",
                    },
                }
            ]
        }
        self._mock_response(mock_requests, api_data)

        result = lookup("443061841")

        assert result["name"] == "GOOGLE FRANCE"
        assert result["siren"] == "443061841"
        assert result["siret"] == "44306184100047"
        assert result["vat_number"].startswith("FR")
        assert result["address_line1"] == "8 RUE DE LONDRES"
        assert result["address_postcode"] == "75009"
        assert result["address_city"] == "PARIS"
        assert result["address_country"] == "FR"
        assert result["etat_administratif"] == "A"

    def test_success_siret_matching_etablissement(self, mock_requests):
        """When querying a SIRET, should pick the matching etablissement."""
        api_data = {
            "results": [
                {
                    "siren": "443061841",
                    "nom_complet": "GOOGLE FRANCE",
                    "etat_administratif": "A",
                    "siege": {
                        "siret": "44306184100047",
                        "numero_voie": "8",
                        "type_voie": "RUE",
                        "libelle_voie": "DE LONDRES",
                        "code_postal": "75009",
                        "libelle_commune": "PARIS",
                    },
                    "matching_etablissements": [
                        {
                            "siret": "44306184100099",
                            "numero_voie": "15",
                            "type_voie": "AVENUE",
                            "libelle_voie": "DE LA LIBERTE",
                            "code_postal": "69003",
                            "libelle_commune": "LYON",
                        }
                    ],
                }
            ]
        }
        self._mock_response(mock_requests, api_data)

        result = lookup("44306184100099")

        assert result["siret"] == "44306184100099"
        assert result["address_line1"] == "15 AVENUE DE LA LIBERTE"
        assert result["address_postcode"] == "69003"
        assert result["address_city"] == "LYON"

    def test_siret_no_matching_etablissement(self, mock_requests):
        """When SIRET not in matching_etablissements, keep the SIRET but use siege address."""
        api_data = {
            "results": [
                {
                    "siren": "443061841",
                    "nom_complet": "GOOGLE FRANCE",
                    "etat_administratif": "A",
                    "siege": {
                        "siret": "44306184100047",
                        "numero_voie": "8",
                        "type_voie": "RUE",
                        "libelle_voie": "DE LONDRES",
                        "code_postal": "75009",
                        "libelle_commune": "PARIS",
                    },
                    "matching_etablissements": [],
                }
            ]
        }
        self._mock_response(mock_requests, api_data)

        result = lookup("44306184199999")

        # SIRET from the query is used, address from siege
        assert result["siret"] == "44306184199999"
        assert result["address_line1"] == "8 RUE DE LONDRES"

    def test_no_results_raises(self, mock_requests):
        self._mock_response(mock_requests, {"results": []})

        with pytest.raises(SireneNotFoundError, match="Aucune entreprise"):
            lookup("123456789")

    def test_no_results_none_raises(self, mock_requests):
        self._mock_response(mock_requests, {"results": None})

        with pytest.raises(SireneNotFoundError, match="Aucune entreprise"):
            lookup("123456789")

    def test_api_connection_error(self, mock_requests):
        import requests

        mock_requests.get.side_effect = requests.ConnectionError("Network error")
        mock_requests.RequestException = requests.RequestException

        with pytest.raises(SireneError, match="Impossible de contacter"):
            lookup("123456789")

    def test_api_http_error(self, mock_requests):
        import requests

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
        mock_requests.get.return_value = mock_resp
        mock_requests.RequestException = requests.RequestException

        with pytest.raises(SireneError, match="Impossible de contacter"):
            lookup("123456789")

    def test_invalid_query_raises_before_api_call(self, mock_requests):
        with pytest.raises(SireneError, match="SIREN.*SIRET"):
            lookup("bad")

        mock_requests.get.assert_not_called()

    def test_empty_siren_returns_empty_vat(self, mock_requests):
        """When siren is empty in the API response, vat_number should be empty."""
        api_data = {
            "results": [
                {
                    "siren": "",
                    "nom_complet": "Unknown",
                    "siege": {},
                }
            ]
        }
        self._mock_response(mock_requests, api_data)

        result = lookup("123456789")
        assert result["vat_number"] == ""
