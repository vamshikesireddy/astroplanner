import pytest
import math
import app


def test_perfect_score():
    score, quality = app._exoplanet_score("Complete", 50.0, 90.0, 3.5)
    assert score == 100
    assert quality == "HIGH"


def test_skip_quality():
    score, quality = app._exoplanet_score("Partial", 15.0, 5.0, 0.5)
    assert score < 30
    assert quality == "SKIP"


def test_complete_adds_more_than_partial():
    score_c, _ = app._exoplanet_score("Complete", 35.0, 45.0, 2.0)
    score_p, _ = app._exoplanet_score("Partial",  35.0, 45.0, 2.0)
    assert score_c > score_p


def test_quality_high():
    _, q = app._exoplanet_score("Complete", 50.0, 90.0, 3.5)
    assert q == "HIGH"


def test_quality_med():
    # Complete(30) + alt30(20) + moon40(15) + dur2h(10) = 75 -- HIGH
    # Partial(15) + alt30(20) + moon40(15) + dur2h(10) = 60 -- MED
    score, q = app._exoplanet_score("Partial", 30.0, 40.0, 2.0)
    assert score == 60
    assert q == "MED"


def test_quality_low():
    # Partial(15) + alt20(10) + moon35(15) + dur1h(5) = 45 -- LOW
    score, q = app._exoplanet_score("Partial", 22.0, 35.0, 1.0)
    assert score == 45
    assert q == "LOW"


def test_quality_skip():
    # Partial(15) + alt<20(0) + moon<10(0) + dur<1h(0) = 15 -- SKIP
    _, q = app._exoplanet_score("Partial", 18.0, 8.0, 0.5)
    assert q == "SKIP"


def test_nan_altitude_gives_zero_alt_points():
    score_nan, _ = app._exoplanet_score("Complete", float("nan"), 90.0, 3.5)
    score_low, _ = app._exoplanet_score("Complete", 5.0, 90.0, 3.5)
    assert score_nan == score_low


def test_nan_moon_sep_gives_zero_moon_points():
    score_nan, _ = app._exoplanet_score("Complete", 50.0, float("nan"), 3.5)
    score_low, _ = app._exoplanet_score("Complete", 50.0, 5.0, 3.5)
    assert score_nan == score_low


def test_score_is_int():
    score, _ = app._exoplanet_score("Complete", 45.0, 70.0, 2.5)
    assert isinstance(score, int)
