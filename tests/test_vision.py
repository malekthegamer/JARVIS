"""Slice-5 vision fallback. Stage 1: pure logic (coord mapping, bounds, JSON
parse, tier union) + the orchestrator with BOTH external seams mocked
(_grab_window, _call_gemini) — no screen, no model. Stage 2 adds live tests."""
from __future__ import annotations

import numpy as np
import pytest

from jarvis.primitives import vision as jv


# ---------- coordinate mapping (Gemini box = [ymin,xmin,ymax,xmax], 0-1000) ----------

def test_map_box_center_basic():
    # box centre x=(100+300)/2=200 → 200/1000*1000px=200; y=(200+400)/2=300
    assert jv._map_box_to_point([200, 100, 400, 300], (1000, 1000), (0, 0)) == (200, 300)


def test_map_box_center_with_window_offset():
    # 500-wide, 400-tall crop at virtual offset (1000, 500); centre at (50%,50%)
    assert jv._map_box_to_point([500, 500, 500, 500], (500, 400), (1000, 500)) == (1250, 700)


def test_map_box_is_resolution_independent():
    # Normalized coords: same box, a 4K-wide crop → centre scales with width,
    # NOT with any downscale factor (which no longer enters the mapping).
    x, y = jv._map_box_to_point([250, 500, 350, 520], (3840, 2160), (100, 200))
    assert x == 100 + round(510 / 1000 * 3840)   # xmid=510
    assert y == 200 + round(300 / 1000 * 2160)   # ymid=300


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
    _mock_grab(monkeypatch)  # 400x400 crop at offset (100,200)
    # box centre at (15%,15%) of a 400px crop = 60px → +offset = (160,260)
    _mock_model(monkeypatch,
                '{"found": true, "box": [100,100,200,200], "label": "delete", '
                '"risk": "destructive", "confidence": 0.9}')
    r = jv.locate_and_classify("the trash icon", "IconPad")
    assert r["ok"] and r["tier"] == "confirm"
    assert r["point"] == (160, 260)
    assert r["window_title"] == "IconPad"


def test_locate_found_safe_auto(monkeypatch):
    _mock_grab(monkeypatch)
    _mock_model(monkeypatch,
                '{"found": true, "box": [100,100,200,200], "label": "bold", '
                '"risk": "safe", "confidence": 0.92}')
    r = jv.locate_and_classify("the bold icon", "IconPad")
    assert r["ok"] and r["tier"] == "auto"


def test_locate_out_of_bounds_rejected(monkeypatch):
    _mock_grab(monkeypatch, rect=(100, 200, 150, 250))  # tiny 50x50 rect...
    # ...but the mock image from _mock_grab is 400x400, so a box centre at 90%
    # maps to ~360px → (460,560), far outside the declared rect → rejected.
    _mock_model(monkeypatch,
                '{"found": true, "box": [900,900,900,900], "label": "x", '
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


# ---------- stage 2: LIVE vision round-trip on constructed images ----------
# Proves the real prompt + response_schema + parse + map + tier work against
# actual Gemini. Key-gated and slow — skipped without a key.

from jarvis import config

live = pytest.mark.skipif(not config.get_api_key("gemini"),
                          reason="GEMINI_API_KEY not configured")

RECT = (0, 0, 240, 240)   # image drawn at virtual origin, 240x240


def _font(sz):
    from PIL import ImageFont
    for n in ("arialbd.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(n, sz)
        except Exception:
            pass
    return ImageFont.load_default()


def _icon_image(kind: str):
    """Return a BGR np array of a clearly-drawn icon on a light toolbar bg.
    _grab_window returns BGR (the code flips BGR→RGB), so we hand back BGR."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (240, 240), (245, 246, 248))
    d = ImageDraw.Draw(im)
    if kind == "trash":
        # trash can: lid + handle + tapered body + vertical stripes
        d.rectangle([70, 60, 170, 74], fill=(60, 63, 70))          # lid
        d.rectangle([104, 48, 136, 60], fill=(60, 63, 70))         # handle
        d.polygon([(80, 78), (160, 78), (150, 190), (90, 190)], fill=(80, 84, 92))  # body
        for x in (108, 120, 132):
            d.line([(x, 92), (x, 176)], fill=(245, 246, 248), width=4)  # stripes
    elif kind == "bold":
        # a button-like bold 'B' (a bare glyph isn't recognized as a control)
        d.rectangle([60, 60, 180, 180], fill=(255, 255, 255),
                    outline=(120, 124, 130), width=3)
        d.text((92, 78), "B", fill=(20, 22, 26), font=_font(90))
    import numpy as np
    rgb = np.asarray(im)
    return rgb[:, :, ::-1].copy()  # RGB → BGR


def _mock_grab_image(monkeypatch, kind):
    img = _icon_image(kind)
    monkeypatch.setattr(jv, "_grab_window",
                        lambda window_hint: (img, (RECT[0], RECT[1]), RECT, "IconPad"))


@live
def test_live_trash_icon_is_confirm(monkeypatch):
    _mock_grab_image(monkeypatch, "trash")
    r = jv.locate_and_classify("the delete or trash button", "IconPad")
    assert r["ok"], r
    assert jv._point_in_rect(r["point"], RECT)
    assert r["tier"] == "confirm", f"trash must gate; got {r}"


@live
def test_live_bold_icon_is_auto(monkeypatch):
    _mock_grab_image(monkeypatch, "bold")
    r = jv.locate_and_classify("the bold text formatting button", "IconPad")
    assert r["ok"], r
    assert jv._point_in_rect(r["point"], RECT)
    assert r["tier"] == "auto", f"bold formatting should be AUTO; got {r}"


@live
def test_live_confabulation_is_documented_and_still_gated(monkeypatch):
    """HONEST characterization: on a BLANK image the model confabulates a
    control (verified: invents 'send message' at confidence 1.0) — prompt
    engineering does not stop it, so we do NOT assert no-match here. What we
    DO require is the safety property: if it confabulates a risky control, the
    tier is still CONFIRM (the gate is the backstop, per the module's KNOWN
    LIMITATION note). The honest no-match code path is covered by the mocked
    test_locate_no_match; the execution-time from_point guard (stage 3) is the
    other defense."""
    blank = np.full((240, 240, 3), 243, dtype=np.uint8)
    monkeypatch.setattr(jv, "_grab_window",
                        lambda window_hint: (blank, (0, 0), RECT, "IconPad"))
    r = jv.locate_and_classify("the send or delete button", "IconPad")
    if r["ok"]:  # confabulated (the common case) → must be gated, not AUTO
        assert r["tier"] == "confirm", f"a confabulated risky control must gate: {r}"
    # if the model happened to say not-found, that's fine too — either is safe.
