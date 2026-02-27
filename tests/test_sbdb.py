# tests/test_sbdb.py
import pytest
import requests
from unittest.mock import patch, MagicMock
from backend.sbdb import sbdb_lookup


def test_sbdb_lookup_found():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"object": {"spkid": "90004812", "des": "C/2025 Q3"}}
    mock_resp.raise_for_status.return_value = None
    with patch("backend.sbdb.requests.get", return_value=mock_resp):
        result = sbdb_lookup("C/2025 Q3")
    assert result == "90004812"


def test_sbdb_lookup_not_found():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"message": "no object found"}
    mock_resp.raise_for_status.return_value = None
    with patch("backend.sbdb.requests.get", return_value=mock_resp):
        result = sbdb_lookup("nonexistent comet xyz")
    assert result is None


def test_sbdb_lookup_timeout():
    with patch("backend.sbdb.requests.get", side_effect=requests.exceptions.Timeout):
        result = sbdb_lookup("C/2025 Q3")
    assert result is None


def test_sbdb_lookup_malformed_json():
    mock_resp = MagicMock()
    mock_resp.json.side_effect = ValueError("No JSON object")
    mock_resp.raise_for_status.return_value = None
    with patch("backend.sbdb.requests.get", return_value=mock_resp):
        result = sbdb_lookup("C/2025 Q3")
    assert result is None


def test_sbdb_lookup_http_error():
    with patch("backend.sbdb.requests.get", side_effect=requests.exceptions.ConnectionError):
        result = sbdb_lookup("anything")
    assert result is None


def test_sbdb_lookup_multiple_matches_picks_primary():
    """HTTP 300 with multiple matches → pick first pdes → recurse to get SPK-ID."""
    # First call: multi-match 300 response
    mock_300 = MagicMock()
    mock_300.status_code = 300
    mock_300.json.return_value = {
        "code": "300",
        "list": [
            {"pdes": "2025 K1",   "name": "C/2025 K1 (ATLAS)"},
            {"pdes": "2025 K1-B", "name": "C/2025 K1-B (ATLAS)"},
        ]
    }
    mock_300.raise_for_status.return_value = None

    # Second call (recursive): single match for "2025 K1"
    mock_200 = MagicMock()
    mock_200.status_code = 200
    mock_200.json.return_value = {"object": {"spkid": "90004567", "des": "2025 K1"}}
    mock_200.raise_for_status.return_value = None

    with patch("backend.sbdb.requests.get", side_effect=[mock_300, mock_200]):
        result = sbdb_lookup("C/2025 K1")
    assert result == "90004567"


def test_sbdb_lookup_300_with_empty_list():
    """HTTP 300 with empty list → return None."""
    mock_resp = MagicMock()
    mock_resp.status_code = 300
    mock_resp.json.return_value = {"code": "300", "list": []}
    mock_resp.raise_for_status.return_value = None

    with patch("backend.sbdb.requests.get", return_value=mock_resp):
        result = sbdb_lookup("ambiguous name")
    assert result is None
