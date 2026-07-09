"""Slice-5 vision fallback. Stage 1: pure logic (coord mapping, bounds, JSON
parse, tier union) + the orchestrator with BOTH external seams mocked
(_grab_window, _call_gemini) — no screen, no model. Stage 2 adds live tests."""
from __future__ import annotations

import numpy as np
import pytest

from jarvis.primitives import vision as jv


# ---------- coordinate mapping ----------

def test_map_box_center_no_downscale():
    # box in image px, scale 1.0, zero offset → plain centre
    assert jv._map_box_to_point([10, 20, 30, 40], 1.0, (0, 0)) == (20, 30)


def test_map_box_center_with_window_offset():
    # window's top-left is (1000, 500) in virtual-screen coords
    assert jv._map_box_to_point([0, 0, 100, 50], 1.0, (1000, 500)) == (1050, 525)


def test_map_box_4k_downscale():
    # 3840-wide crop downscaled to 1024 → scale ≈ 0.26667; a centre at
    # downscaled x=512 maps back to ~1920 in the original, + offset.
    scale = 1024 / 3840
    x, y = jv._map_box_to_point([500, 0, 524, 24], scale, (100, 200))
    assert 1990 <= x <= 2020, x   # (512 / scale) + 100 ≈ 1920+100
    assert y == 200 + round(12 / scale)


# ---------- bounds validation ----------

def test_point_in_rect_inside():
    assert jv._point_in_rect((150, 250), (100, 200, 300, 400)) is True


@pytest.mark.parametrize("pt", [(50, 250), (350, 250), (150, 100), (150, 500)])
def test_point_out_of_rect(pt):
    assert jv._point_in_rect(pt, (100, 200, 300, 400)) is False


# ---------- JSON parse ----------

def test_parse_found():
    d = jv._parse_vision_json(
        '{"found": true, "box": [1,2,3,4], "label": "delete", '
        '"risk": "destructive", "confidence": 0.9}')
    assert d["found"] and d["box"] == [1.0, 2.0, 3.0, 4.0]
    assert d["label"] == "delete" and d["risk"] == "destructive"


def test_parse_not_found():
    assert jv._parse_vision_json('{"found": false}')["found"] is False


def test_parse_malformed_returns_none():
    assert jv._parse_vision_json("not json at all") is None
    assert jv._parse_vision_json('{"found": true}') is None  # found but no box
    assert jv._parse_vision_json('{"found": true, "box": [1,2,3]}') is None  # short box


# ---------- tier union (the safety crux) ----------

@pytest.mark.parametrize("label,risk,conf,expected", [
    ("trash",      "destructive", 0.95, "confirm"),  # vision risk flag
    ("send",       "committal",   0.95, "confirm"),
    ("delete",     "safe",        0.95, "confirm"),  # regex catches it even if risk=safe
    ("bold",       "safe",        0.95, "auto"),     # both signals safe → AUTO
    ("mystery",    "unsure",      0.95, "confirm"),  # unsure → fail closed
    ("bold",       "safe",        0.20, "confirm"),  # low confidence → fail closed
    ("italic",     "safe",        None, "confirm"),  # missing confidence → fail closed
])
def test_tier_union(label, risk, conf, expected):
    assert jv._tier_for(label, risk, conf, min_confidence=0.5) == expected


# ---------- downscale/encode ----------

def test_downscale_shrinks_large_image():
    big = np.zeros((400, 2000, 3), dtype=np.uint8)   # 2000px wide
    png, scale = jv._downscale_and_encode(big, max_edge=1000)
    assert 0.49 < scale < 0.51 and isinstance(png, bytes) and len(png) > 0


def test_no_upscale_small_image():
    small = np.zeros((100, 200, 3), dtype=np.uint8)
    png, scale = jv._downscale_and_encode(small, max_edge=1000)
    assert scale == 1.0


# ---------- orchestrator (both seams mocked) ----------

def _mock_grab(monkeypatch, rect=(100, 200, 500, 600), title="IconPad"):
    # W=400,H=400 image at offset (100,200); no downscale at max_edge 1024
    img = np.zeros((400, 400, 3), dtype=np.uint8)
    monkeypatch.setattr(jv, "_grab_window",
                        lambda window_hint: (img, (rect[0], rect[1]), rect, title))


def _mock_model(monkeypatch, payload: str | None):
    monkeypatch.setattr(jv, "_call_gemini", lambda png, description: payload)


def test_locate_disabled_short_circuits(monkeypatch):
    from jarvis.core.settings_store import settings
    settings.set("vision.enabled", False, persist=False)
    called = {"n": 0}
    monkeypatch.setattr(jv, "_grab_window",
                        lambda w: called.__setitem__("n", called["n"] + 1))
    try:
        r = jv.locate_and_classify("anything", "IconPad")
    finally:
        settings.set("vision.enabled", True, persist=False)
    assert r["ok"] is False and called["n"] == 0  # never even captured


def test_locate_found_destructive(monkeypatch):
    _mock_grab(monkeypatch)
    _mock_model(monkeypatch,
                '{"found": true, "box": [40,40,80,80], "label": "delete", '
                '"risk": "destructive", "confidence": 0.9}')
    r = jv.locate_and_classify("the trash icon", "IconPad")
    assert r["ok"] and r["tier"] == "confirm"
    # centre (60,60) + offset (100,200) = (160,260), inside the rect
    assert r["point"] == (160, 260)
    assert r["window_title"] == "IconPad"


def test_locate_found_safe_auto(monkeypatch):
    _mock_grab(monkeypatch)
    _mock_model(monkeypatch,
                '{"found": true, "box": [40,40,80,80], "label": "bold", '
                '"risk": "safe", "confidence": 0.92}')
    r = jv.locate_and_classify("the bold icon", "IconPad")
    assert r["ok"] and r["tier"] == "auto"


def test_locate_out_of_bounds_rejected(monkeypatch):
    _mock_grab(monkeypatch, rect=(100, 200, 150, 250))  # tiny rect
    _mock_model(monkeypatch,
                '{"found": true, "box": [300,300,340,340], "label": "x", '
                '"risk": "safe", "confidence": 0.9}')
    r = jv.locate_and_classify("something", "IconPad")
    assert r["ok"] is False and "outside" in r["reason"].lower()


def test_locate_no_match(monkeypatch):
    _mock_grab(monkeypatch)
    _mock_model(monkeypatch, '{"found": false}')
    r = jv.locate_and_classify("a unicorn", "IconPad")
    assert r["ok"] is False and "find" in r["reason"].lower()


def test_locate_model_unavailable(monkeypatch):
    _mock_grab(monkeypatch)
    _mock_model(monkeypatch, None)
    r = jv.locate_and_classify("x", "IconPad")
    assert r["ok"] is False


def test_locate_malformed_response(monkeypatch):
    _mock_grab(monkeypatch)
    _mock_model(monkeypatch, "the button is on the left somewhere")
    r = jv.locate_and_classify("x", "IconPad")
    assert r["ok"] is False and "unreadable" in r["reason"].lower()


def test_locate_never_raises_on_grab_failure(monkeypatch):
    monkeypatch.setattr(jv, "_grab_window", lambda w: None)
    r = jv.locate_and_classify("x", "IconPad")
    assert r["ok"] is False  # no exception
