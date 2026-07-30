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


# ======================================================================
# SLICE 17 — pre-click point verification.
#
# Slice 16 measured vision LABELLING a control correctly while POINTING at its
# neighbour (asked "paste", answered 'paste content', pointed at the copy icon).
# So the CONFIRM modal can name what you approved while the click lands one icon
# over. verify_point is the last check before the click fires: it asks what is
# ACTUALLY at the point, and refuses if that isn't what was approved.
#
# All seams mocked — no real model, no real UIA.
# ======================================================================

def _mock_uia(monkeypatch, name):
    """UIA from_point returns an element with this accessible name ('' = the
    canvas/icon case, where UIA is blind and the crop re-read must run)."""
    monkeypatch.setattr(jv, "_uia_name_at", lambda point: name)


def _mock_verify_model(monkeypatch, payload):
    """The grounded crop re-read seam. `payload` is the parsed dict or None."""
    calls = {"n": 0}

    def fake(png, approved_label):
        calls["n"] += 1
        return payload
    monkeypatch.setattr(jv, "_call_verify_json", fake)
    return calls


def _verify(point=(300, 400), window="IconPad", approved="paste content"):
    return jv.verify_point(point, window, approved)


def test_verify_uia_named_match_allows_without_model_call(monkeypatch):
    """When UIA CAN name the element (real apps), the check is free — the model
    is never called."""
    _mock_uia(monkeypatch, "Paste content")
    calls = _mock_verify_model(monkeypatch, {"actual_label": "x", "matches": False})
    r = _verify(approved="paste content")
    assert r["verified"] is True, r
    assert calls["n"] == 0, "a UIA name match must skip the model call entirely"


def test_verify_uia_named_mismatch_refuses(monkeypatch):
    _mock_uia(monkeypatch, "Copy")
    _mock_verify_model(monkeypatch, None)
    r = _verify(approved="paste content")
    assert r["verified"] is False
    assert "copy" in r["actual_label"].lower()


def test_verify_crop_match_allows(monkeypatch):
    _mock_grab(monkeypatch)
    _mock_uia(monkeypatch, "")  # canvas: UIA is blind
    _mock_verify_model(monkeypatch, {"actual_label": "paste content", "matches": True})
    r = _verify(approved="paste content")
    assert r["verified"] is True, r


def test_verify_crop_mismatch_refuses(monkeypatch):
    """THE measured bug: approved 'paste content', but 'Copy' is actually there."""
    _mock_grab(monkeypatch)
    _mock_uia(monkeypatch, "")
    _mock_verify_model(monkeypatch, {"actual_label": "Copy", "matches": False})
    r = _verify(approved="paste content")
    assert r["verified"] is False, r
    assert r["actual_label"] == "Copy"
    assert "paste content" in r["reason"] and "Copy" in r["reason"]


def test_verify_risk_escalation_refuses(monkeypatch):
    """Independent cross-check: even if the model SAYS it matches, a benign
    approval must never be waved through onto a destructive control."""
    _mock_grab(monkeypatch)
    _mock_uia(monkeypatch, "")
    _mock_verify_model(monkeypatch, {"actual_label": "delete item", "matches": True})
    r = _verify(approved="zoom in")
    assert r["verified"] is False, r
    assert "delete item" in r["reason"]


def test_verify_model_unavailable_fails_closed(monkeypatch):
    _mock_grab(monkeypatch)
    _mock_uia(monkeypatch, "")
    _mock_verify_model(monkeypatch, None)  # unparseable / model down
    r = _verify()
    assert r["verified"] is False


def test_verify_grab_failure_fails_closed(monkeypatch):
    _mock_uia(monkeypatch, "")
    monkeypatch.setattr(jv, "_grab_window", lambda w: None)
    _mock_verify_model(monkeypatch, {"actual_label": "paste", "matches": True})
    r = _verify()
    assert r["verified"] is False


def test_verify_disabled_setting_passes_through(monkeypatch):
    """Kill switch: verification off → allow (today's behaviour), no model call."""
    from jarvis.core.settings_store import settings
    settings.set("vision.verify_click_point", False, persist=False)
    calls = _mock_verify_model(monkeypatch, None)
    try:
        r = _verify()
    finally:
        settings.set("vision.verify_click_point", True, persist=False)
    assert r["verified"] is True
    assert calls["n"] == 0


@pytest.mark.parametrize("point", [(0, 0), (399, 399), (200, 200)])
def test_crop_around_point_is_clamped_to_image(point):
    img = np.zeros((400, 400, 3), dtype=np.uint8)
    box = jv._crop_around_point(img, point, pad=60)
    x0, y0, x1, y1 = box
    assert 0 <= x0 < x1 <= 400 and 0 <= y0 < y1 <= 400, box


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


# ==================== slice 47: screen-aware Q&A ====================
# A different shape from locate_and_classify: free prose, no response_schema,
# no coordinate mapping — so it answers questions about the screen instead of
# pointing at controls. Whole screen by DEFAULT (the owner's call): if this
# captured the focused window, typing "what am I looking at?" into the HUD would
# capture the HUD and answer "you're looking at JARVIS" — verified that nothing
# excludes the HUD from _foreground_window().
#
# STAGE 0 MEASURED (re-run scratchpad/probe_screen_qa.py, don't trust this):
# at max_edge 1024/1536/1920 the model read a 30px heading, 15px body, 12px
# small print and a dialog message — 4/4 at every size, ~1.3-1.6s. 1024 is
# therefore enough and is the default.

def _fake_screen(w=400, h=300):
    return np.zeros((h, w, 3), dtype=np.uint8)


def test_answer_about_screen_captures_the_whole_screen_when_no_hint(monkeypatch):
    """The DEFAULT path must be the whole desktop, NOT the focused window."""
    calls = {"whole": 0, "window": 0}

    def fake_capture(monitor=0):
        calls["whole"] += 1
        return _fake_screen()

    monkeypatch.setattr(jv.screen, "capture_screen", fake_capture)
    monkeypatch.setattr(jv, "_grab_window",
                        lambda hint: calls.__setitem__("window", calls["window"] + 1))
    monkeypatch.setattr(jv, "_call_gemini_qa", lambda png, q: "a code editor")

    r = jv.answer_about_screen("what am I looking at?")
    assert r["ok"] and r["answer"] == "a code editor", r
    assert calls["whole"] == 1, "must capture the whole screen"
    assert calls["window"] == 0, "must NOT use the focused-window path"


def test_answer_about_screen_captures_the_named_window_when_hinted(monkeypatch):
    seen = {}
    monkeypatch.setattr(jv, "_grab_window",
                        lambda hint: (seen.setdefault("hint", hint),
                                      (_fake_screen(), (0, 0), RECT, "Notepad"))[1])
    monkeypatch.setattr(jv.screen, "capture_screen",
                        lambda monitor=0: pytest.fail("must not grab whole screen"))
    monkeypatch.setattr(jv, "_call_gemini_qa", lambda png, q: "a text file")

    r = jv.answer_about_screen("what does it say?", window_hint="Notepad")
    assert r["ok"] and r["answer"] == "a text file", r
    assert seen["hint"] == "Notepad"
    assert "Notepad" in r["source"], f"source must name the window: {r}"


def test_answer_about_screen_does_not_steal_focus_on_the_whole_screen_path(monkeypatch):
    """capture_screen() must be used precisely because it does NOT set_focus().
    Asking a question should never rearrange the user's desktop."""
    monkeypatch.setattr(jv.screen, "capture_screen", lambda monitor=0: _fake_screen())
    monkeypatch.setattr(jv, "_grab_window",
                        lambda hint: pytest.fail("_grab_window focuses — not on this path"))
    monkeypatch.setattr(jv, "_call_gemini_qa", lambda png, q: "ok")
    assert jv.answer_about_screen("what is this?")["ok"]


def test_answer_about_screen_fails_closed_when_vision_disabled(monkeypatch):
    from jarvis.core.settings_store import settings
    monkeypatch.setattr(settings, "get",
                        lambda k, d=None: False if k == "vision.enabled" else d)
    monkeypatch.setattr(jv.screen, "capture_screen",
                        lambda monitor=0: pytest.fail("must not capture when disabled"))
    r = jv.answer_about_screen("what am I looking at?")
    assert r["ok"] is False and "disabled" in r["reason"]


def test_answer_about_screen_fails_closed_when_capture_fails(monkeypatch):
    def boom(monitor=0):
        raise RuntimeError("no display")
    monkeypatch.setattr(jv.screen, "capture_screen", boom)
    r = jv.answer_about_screen("what am I looking at?")
    assert r["ok"] is False and "capture" in r["reason"].lower()


def test_answer_about_screen_fails_closed_when_the_window_is_missing(monkeypatch):
    monkeypatch.setattr(jv, "_grab_window", lambda hint: None)
    r = jv.answer_about_screen("what does it say?", window_hint="Nope")
    assert r["ok"] is False and "capture" in r["reason"].lower()


def test_answer_about_screen_fails_closed_when_the_model_is_unavailable(monkeypatch):
    monkeypatch.setattr(jv.screen, "capture_screen", lambda monitor=0: _fake_screen())
    monkeypatch.setattr(jv, "_call_gemini_qa", lambda png, q: None)
    r = jv.answer_about_screen("what am I looking at?")
    assert r["ok"] is False and "unavailable" in r["reason"]


@pytest.mark.parametrize("empty", ["", "   ", "\n"])
def test_answer_about_screen_fails_closed_on_an_empty_answer(monkeypatch, empty):
    monkeypatch.setattr(jv.screen, "capture_screen", lambda monitor=0: _fake_screen())
    monkeypatch.setattr(jv, "_call_gemini_qa", lambda png, q: empty)
    r = jv.answer_about_screen("what am I looking at?")
    assert r["ok"] is False, f"blank answer must fail closed, got {r}"


def test_qa_uses_its_own_max_edge_not_the_click_paths(monkeypatch):
    """RISK 1: vision.max_edge_px=1024 is load-bearing for slice 16/17's
    PUBLISHED accuracy. Tuning the Q&A path must never drag the click path with
    it, so they read different settings."""
    from jarvis.core.settings_store import settings
    seen = {}

    def fake_encode(img, max_edge):
        seen["max_edge"] = max_edge
        return b"png", 1.0

    monkeypatch.setattr(settings, "get", lambda k, d=None: {
        "vision.enabled": True,
        "vision.qa_max_edge_px": 1536,
        "vision.max_edge_px": 1024,
    }.get(k, d))
    monkeypatch.setattr(jv.screen, "capture_screen", lambda monitor=0: _fake_screen())
    monkeypatch.setattr(jv, "_downscale_and_encode", fake_encode)
    monkeypatch.setattr(jv, "_call_gemini_qa", lambda png, q: "ok")

    jv.answer_about_screen("what am I looking at?")
    assert seen["max_edge"] == 1536, \
        f"Q&A must use vision.qa_max_edge_px, got {seen['max_edge']}"


def test_answer_about_screen_never_raises(monkeypatch):
    """Every seam fails closed; nothing propagates to the agent loop."""
    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(jv.screen, "capture_screen", lambda monitor=0: _fake_screen())
    monkeypatch.setattr(jv, "_downscale_and_encode", boom)
    r = jv.answer_about_screen("what am I looking at?")
    assert r["ok"] is False


# ---------- slice 47 stage 3: LIVE screen Q&A on a constructed screen ----------
# Same trick as the live locate tests above: the IMAGE is synthetic (so the
# assertion is deterministic and the desktop is never driven — this module stays
# out of conftest's _DESKTOP_DRIVING_MODULES), but the MODEL CALL IS REAL. That
# is what proves the prompt actually reads a screen, which no mock can.

_DOC_HEADING = "Quarterly Revenue Report"
_DOC_COMPANY = "Northwind"
_DOC_INVOICE = "INV-88421"
_DLG_CODE = "0x8007045D"


def _screen_image():
    """A 1920x1080 'desktop': a document window + an error dialog. Facts are
    planted at 30px/15px/12px so the test proves SMALL text is readable, not
    just headlines. Returns BGR (capture_screen's format)."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (1920, 1080), (32, 34, 40))
    d = ImageDraw.Draw(im)
    d.rectangle([120, 90, 1180, 900], fill=(255, 255, 255))
    d.rectangle([120, 90, 1180, 130], fill=(230, 232, 236))
    d.text((136, 100), "report.docx - Word", font=_font(15), fill=(40, 40, 40))
    d.text((160, 170), _DOC_HEADING, font=_font(30), fill=(15, 15, 15))
    lines = [
        (f"Prepared for {_DOC_COMPANY} Traders Ltd for the period ending March.", 15),
        ("Total revenue rose 18% year on year, driven by the retail channel.", 15),
        (f"Outstanding invoice reference {_DOC_INVOICE} remains unpaid.", 12),
    ]
    y = 240
    for text, size in lines:
        d.text((160, y), text, font=_font(size), fill=(30, 30, 30))
        y += 40
    d.rectangle([1250, 380, 1800, 620], fill=(240, 240, 244))
    d.rectangle([1250, 380, 1800, 420], fill=(200, 60, 60))
    d.text((1266, 390), "Disk Write Error", font=_font(16), fill=(255, 255, 255))
    d.text((1276, 460), f"Cannot save to drive E: (code {_DLG_CODE})",
           font=_font(14), fill=(20, 20, 20))
    import numpy as np
    return np.asarray(im)[:, :, ::-1].copy()


@live
def test_live_screen_query_reads_text_from_the_screen(monkeypatch):
    """The real thing: a real model call must read planted text off a screen.
    12px small print is included deliberately — Stage 0 measured it readable at
    max_edge=1024 (~6px after downscale), so a regression here is real."""
    monkeypatch.setattr(jv.screen, "capture_screen", lambda monitor=0: _screen_image())
    r = jv.answer_about_screen(
        "What is the document's heading, which company is it prepared for, "
        "and what is the outstanding invoice reference?")
    assert r["ok"], r
    a = r["answer"].lower()
    assert _DOC_HEADING.lower() in a, f"missed the 30px heading: {r['answer']}"
    assert _DOC_COMPANY.lower() in a, f"missed the 15px body text: {r['answer']}"
    assert _DOC_INVOICE.lower() in a, f"missed the 12px small print: {r['answer']}"
    assert r["source"] == "the whole screen"


@live
def test_live_screen_query_describes_a_dialog(monkeypatch):
    """'What does this error say?' — the case with no accessible text layer,
    which is exactly why a vision path exists at all."""
    monkeypatch.setattr(jv.screen, "capture_screen", lambda monitor=0: _screen_image())
    r = jv.answer_about_screen("What does the error dialog say?")
    assert r["ok"], r
    a = r["answer"].lower()
    assert _DLG_CODE.lower() in a or "disk write error" in a, \
        f"didn't read the dialog: {r['answer']}"


@live
def test_live_screen_query_admits_when_the_answer_is_not_on_screen(monkeypatch):
    """Confabulation guard. Asked about something absent, it must say so rather
    than invent — the residual vision.py documents, checked on THIS path."""
    monkeypatch.setattr(jv.screen, "capture_screen", lambda monitor=0: _screen_image())
    r = jv.answer_about_screen("What is the user's bank account balance?")
    assert r["ok"], r
    a = r["answer"].lower()
    admits = any(w in a for w in
                 ("no ", "not ", "n't", "cannot", "can't", "unable", "isn't",
                  "does not", "nothing"))
    assert admits, f"must admit the answer isn't on screen, said: {r['answer']}"
