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
