"""Vision FALLBACK for element targeting (spec §1.2 find_element vision path).

Engaged only when the fast text path (read_ui_tree + classify_click by name)
cannot identify an element — e.g. an icon-only button with no accessible
name. Sends the target WINDOW's screenshot to Gemini and asks it, with a
constrained structured-JSON prompt, to locate the described control and say
what it does. The returned point feeds the existing click primitive; the
returned semantics feed the SAME AUTO/CONFIRM classifier — vision is a second
entrance to the safety gate, never a bypass.

Everything here fails closed and never raises: any capture/model/parse
problem returns {"ok": False, "reason": ...} so the caller refuses to click
rather than clicking blind.

MEASURED ACCURACY (slice 16, `tests/harness_vision_eval.py` against the VisionPad
golden set — re-run it, don't trust this comment):

  localization hit-rate   1.00 easy / 0.88 hard      confabulation  0.00
  tier correctness        1.00                       calls  1   ~7.1 s/locate

- CONFABULATION did NOT reproduce. The slice-5 note claimed the model invents a
  control at confidence 1.0 on an absent/blank target and that prompting can't
  stop it. Measured on a BLANK canvas (the exact condition): 0/9 — it correctly
  returns found=false. The anti-hallucination clause in _PROMPT appears to hold
  on the current model. This is one golden set on one model, NOT a proof of
  absence — keep the downstream defenses.
- ADJACENT-ICON MIS-LOCALIZATION (found slice 16, CLOSED slice 17): on a dense
  toolbar, vision can LABEL correctly while POINTING at the neighbouring control
  (measured: asked for "the paste icon" it answered 'paste content' but pointed
  at the adjacent copy icon). Left unchecked, the CONFIRM modal would name the
  control you approved while the click landed one icon over.

  *** CORRECTION — slice 16 claimed here that "a second look does not fix this;
  it is a perception disagreement, not a hallucination, so the model re-confirms
  the same answer". That was an INFERENCE I never tested, and it is FALSE.
  Measured in slice 17: a NON-LEADING re-read of a tight crop at the actual point
  names it 'Copy' 3/3. The original locate asks a LEADING question ("find the
  paste icon") over the whole window, which biases the label; asking "what IS
  this?" over an isolated crop gets the truth. That distinction is the whole
  mechanism, and it is what verify_point() below exploits. ***

  Now closed by verify_point() (slice 17), measured: wrong-click rate 0.042 → 0.0
  (catch rate 1.0) at a false-refusal cost of 0.023 and ~2× latency on the vision
  path only.
- The downstream defenses still stand: (1) the CONFIRM gate; (2) the
  execution-time from_point hit-test. NOTE the honest limit of (2): it verifies
  that SOMETHING clickable is at the point, NOT that it matches the approved
  label — which is exactly why verify_point() exists.
- Confidence thresholding remains useless against an overconfident model, so
  min_confidence only guards the honest low-confidence case.
- Tiering shares ONE vocabulary with the fast path (input.is_committal_name):
  English + i18n + CJK, so a vision-read "Löschen"/"Print" gates like "Delete".
"""
from __future__ import annotations

import json
import re

from jarvis import config
from jarvis.core.settings_store import settings

_VISION_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "box": {"type": "array", "items": {"type": "number"}},  # [x0,y0,x1,y1] in image px
        "label": {"type": "string"},   # short: what the control DOES
        "risk": {"type": "string", "enum": ["destructive", "committal", "safe", "unsure"]},
        "confidence": {"type": "number"},
    },
    "required": ["found"],
}

_PROMPT = (
    "You are a precise UI element locator. The image is a screenshot of a single "
    "application window. Find the control that best matches this description:\n"
    '  "{description}"\n'
    "Return JSON only. If you find it, set found=true and give:\n"
    "- box: the control's bounding box as [ymin, xmin, ymax, xmax], four integers "
    "0-1000 normalized to the image size (the standard Gemini box format).\n"
    "- label: 2-4 words for what the control DOES (e.g. 'delete item', 'bold text', "
    "'send message', 'close window'). Describe the actual control you see, not the "
    "description above.\n"
    "- risk: 'destructive' (deletes/erases/removes), 'committal' (sends/submits/saves/"
    "buys/posts — hard to undo), 'safe' (formatting/navigation/selection — easily "
    "reversible), or 'unsure' if you cannot tell what it does.\n"
    "- confidence: 0.0-1.0, how sure you are this is the right control.\n"
    "CRITICAL: only set found=true for a control you can ACTUALLY SEE in the image. "
    "If the image is blank, or contains no control matching the description, you MUST "
    "set found=false. Do NOT invent, guess, or hallucinate a control that is not "
    "visibly present — a wrong location is worse than admitting it isn't there."
)

_cached_client = None
_cached_key: str | None = None


# ============================ pure helpers (unit-tested) ============================

def _map_box_to_point(box, img_wh, offset_xy) -> tuple[int, int]:
    """Gemini returns [ymin, xmin, ymax, xmax] normalized to 0-1000. Map the
    box centre to an absolute virtual-screen point using the crop's pixel size
    and its top-left offset. Normalized coords are resolution-independent, so
    the downscale factor deliberately does NOT enter here."""
    ymin, xmin, ymax, xmax = (float(v) for v in box)
    w, h = img_wh
    cx = (xmin + xmax) / 2.0 / 1000.0 * w
    cy = (ymin + ymax) / 2.0 / 1000.0 * h
    ox, oy = offset_xy
    return (int(round(ox + cx)), int(round(oy + cy)))


def _point_in_rect(point, rect) -> bool:
    x, y = point
    left, top, right, bottom = rect
    return left <= x <= right and top <= y <= bottom


def _parse_vision_json(text: str) -> dict | None:
    """Defensive parse. None on anything unreadable (fail closed upstream)."""
    try:
        d = json.loads(text)
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    if not d.get("found"):
        return {"found": False}
    box = d.get("box")
    if not (isinstance(box, list) and len(box) == 4
            and all(isinstance(n, (int, float)) for n in box)):
        return None
    conf = d.get("confidence")
    return {
        "found": True,
        "box": [float(n) for n in box],
        "label": str(d.get("label", "")),
        "risk": str(d.get("risk", "unsure")),
        "confidence": float(conf) if isinstance(conf, (int, float)) else None,
    }


def _tier_for(label: str, risk: str, confidence, min_confidence: float) -> str:
    """The union rule (see plan 'the crux'). Any risky signal, or any
    uncertainty, → confirm. AUTO only when clearly identified AND safe."""
    from jarvis.primitives.input import is_committal_name  # one classifier, two sources

    risk = (risk or "").lower()
    if risk in ("destructive", "committal"):
        return "confirm"
    if risk == "unsure":
        return "confirm"
    if confidence is None or confidence < min_confidence:
        return "confirm"  # fail closed on uncertainty — never click an unknown control
    # Slice 16: the SAME vocabulary the fast path uses (English + i18n + CJK), so a
    # vision-read "Löschen"/"Print" gates exactly like a text-labelled "Delete".
    if is_committal_name(label or ""):
        return "confirm"
    return "auto"


def _downscale_and_encode(img_bgr, max_edge: int) -> tuple[bytes, float]:
    """(H,W,3) BGR array → (PNG bytes, scale). scale = new/old edge (≤1.0)."""
    from PIL import Image

    h, w = img_bgr.shape[:2]
    rgb = img_bgr[:, :, ::-1]  # BGR → RGB
    im = Image.fromarray(rgb)
    longest = max(h, w)
    scale = 1.0
    if longest > max_edge:
        scale = max_edge / longest
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    import io
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue(), scale


# ============================ external seams (mocked in unit tests) ============================

def _grab_window(window_hint: str | None):
    """(img_bgr, offset_xy, rect, title) for the target window, or None.

    mss grabs the window's exact rectangle in absolute virtual-screen coords,
    so multi-monitor origins need no offset math — offset is just the rect's
    top-left, used to map image coords back to screen coords."""
    import time
    from jarvis.primitives.input import _target_window
    win, title = _target_window(window_hint)
    if win is None:
        return None
    try:
        # Bring the window forward so the screenshot is of the ACTUAL target,
        # not whatever happens to overlap its rectangle. Best-effort.
        try:
            win.set_focus()
            time.sleep(0.15)
        except Exception:
            pass
        r = win.rectangle()
        left, top, right, bottom = r.left, r.top, r.right, r.bottom
        w, h = right - left, bottom - top
        if w <= 0 or h <= 0:
            return None
        import mss
        import numpy as np
        with mss.MSS() as sct:
            shot = sct.grab({"left": left, "top": top, "width": w, "height": h})
        img = np.asarray(shot)[:, :, :3].copy()  # BGRA → BGR
        return img, (left, top), (left, top, right, bottom), title
    except Exception:
        return None


def _call_gemini(png_bytes: bytes, description: str) -> str | None:
    """Structured-JSON multimodal call. Returns the JSON text, or None on any
    failure (missing key, network, SDK error). Reuses the gemini key/timeout
    pattern; a separate cached client since this is a different call shape than
    the tool-calling brain."""
    global _cached_client, _cached_key
    key = config.get_api_key("gemini")
    if not key:
        return None
    try:
        from google import genai
        from google.genai import types
        if _cached_client is None or _cached_key != key:
            _cached_client = genai.Client(api_key=key, http_options={"timeout": 30_000})
            _cached_key = key
        model = settings.get("brain.models.gemini", "gemini-3.1-flash-lite")
        resp = _cached_client.models.generate_content(
            model=model,
            contents=[
                types.Part.from_bytes(data=png_bytes, mime_type="image/png"),
                types.Part.from_text(text=_PROMPT.format(description=description)),
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_VISION_SCHEMA,
            ),
        )
        return resp.text
    except Exception:
        return None


# ==================== slice 17: pre-click point verification ====================
# Slice 16 measured vision LABELLING a control correctly while POINTING at its
# neighbour ("paste" → answered 'paste content', pointed at the copy icon). So the
# CONFIRM modal could name what the user approved while the click landed one icon
# over. This is the last check before the click fires.
#
# TWO TIERS, because on the surface where the bug lives UIA is blind:
#   1. UIA from_point name (FREE) — real apps usually name their controls. If a
#      name is there, compare it and skip the model entirely.
#   2. Grounded crop re-read (1 model call) — for icon/canvas controls, which
#      expose NO accessible name (measured: name='' type='Pane'). This is the ONLY
#      thing that works there.
#
# The re-read question is deliberately NON-LEADING: it names the control FIRST and
# only THEN judges the match. The original locate asks a *leading* question ("find
# the paste icon") over the whole window, which biases the label; asking "what IS
# this?" over an isolated crop gets the truth (measured: says 'Copy' 3/3 at the
# mis-localized point). That distinction is the whole mechanism.
#
# Fail closed everywhere. Costs nothing in practice: locate_and_classify already
# needs the model, so if it's down there was no click to make anyway.

_VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "actual_label": {"type": "string"},
        "matches": {"type": "boolean"},
    },
    "required": ["actual_label", "matches"],
}

_VERIFY_PROMPT = (
    "This image is a zoomed-in crop of a user interface, centred on ONE control.\n"
    "STEP 1 — Look ONLY at the control in the CENTRE of the image. In 2-4 words, "
    "say what THAT control does (e.g. 'copy selection', 'delete item', 'bold "
    "text'). Describe what you actually SEE — do not be influenced by step 2.\n"
    "STEP 2 — Only after naming it, decide: is the centre control THE SAME CONTROL "
    "as this description?\n"
    '  "{approved}"\n'
    "Judge the ACTION, not the wording: different phrasings of the same action "
    "MATCH (e.g. 'open folder' vs 'open file' → matches=true; 'save document' vs "
    "'save' → matches=true). Set matches=false ONLY when the centre control "
    "performs a genuinely DIFFERENT action (e.g. 'copy' when 'paste' was "
    "described). If you are unsure what the centre control is, still name your "
    "best guess, but judge matches on the ACTION alone.\n"
    "Return actual_label (your step-1 answer) and matches."
)


def _uia_name_at(point) -> str:
    """The accessible name of the element at `point`, or '' when there is none
    (icon/canvas controls expose no name — that's why vision runs at all).
    Never raises."""
    try:
        from jarvis.primitives.input import _co_init
        _co_init()
        from pywinauto import Desktop
        el = Desktop(backend="uia").from_point(int(point[0]), int(point[1]))
        return (el.element_info.name or "").strip()
    except Exception:
        return ""


def _crop_around_point(img, point_img_xy, pad: int) -> tuple[int, int, int, int]:
    """A (x0,y0,x1,y1) box of `pad` px around a point, clamped to the image."""
    h, w = img.shape[:2]
    x, y = int(point_img_xy[0]), int(point_img_xy[1])
    x0 = max(0, min(w - 1, x - pad))
    y0 = max(0, min(h - 1, y - pad))
    x1 = max(x0 + 1, min(w, x + pad))
    y1 = max(y0 + 1, min(h, y + pad))
    return x0, y0, x1, y1


def _call_verify_json(png_bytes: bytes, approved_label: str) -> dict | None:
    """The grounded crop re-read (mocked in tests). None on any failure."""
    key = config.get_api_key("gemini")
    if not key:
        return None
    try:
        from google import genai
        from google.genai import types
        global _cached_client, _cached_key
        if _cached_client is None or _cached_key != key:
            _cached_client = genai.Client(api_key=key, http_options={"timeout": 30_000})
            _cached_key = key
        model = settings.get("brain.models.gemini", "gemini-3.1-flash-lite")
        resp = _cached_client.models.generate_content(
            model=model,
            contents=[
                types.Part.from_bytes(data=png_bytes, mime_type="image/png"),
                types.Part.from_text(
                    text=_VERIFY_PROMPT.format(approved=approved_label)),
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_VERIFY_SCHEMA,
            ),
        )
        d = json.loads(resp.text)
        if not isinstance(d, dict) or "matches" not in d:
            return None
        return {"actual_label": str(d.get("actual_label", "")),
                "matches": bool(d.get("matches"))}
    except Exception:
        return None


def _names_agree(a: str, b: str) -> bool:
    """Cheap semantic agreement between two short control labels: a shared
    meaningful action token. Used for the UIA-name tier."""
    stop = {"the", "a", "an", "button", "icon", "control", "item", "items",
            "to", "of", "in", "on", "this", "selected", "selection"}
    ta = {t for t in re.split(r"\W+", (a or "").casefold()) if t and t not in stop}
    tb = {t for t in re.split(r"\W+", (b or "").casefold()) if t and t not in stop}
    return bool(ta & tb)


def verify_point(point, window_hint: str | None, approved_label: str) -> dict:
    """Is the control ACTUALLY at `point` the one the user approved?

    Returns {verified: bool, actual_label: str, reason: str}. Fails CLOSED — any
    error, missing model, or unreadable answer returns verified=False, because a
    click on an unverified control is exactly the bug this closes. Never raises."""
    approved = (approved_label or "").strip()
    if not settings.get("vision.verify_click_point", True):
        return {"verified": True, "actual_label": "", "reason": "verification disabled"}

    # ---- tier 1: UIA name (free; real apps usually have one) ----
    try:
        uia_name = _uia_name_at(point)
    except Exception:
        uia_name = ""
    if uia_name:
        if _names_agree(uia_name, approved):
            return {"verified": True, "actual_label": uia_name,
                    "reason": f"UIA confirms '{uia_name}' at the point"}
        return {"verified": False, "actual_label": uia_name,
                "reason": (f"you approved '{approved}', but '{uia_name}' is the "
                           f"control at that point — not clicking")}

    # ---- tier 2: grounded crop re-read (icon/canvas: UIA is blind here) ----
    try:
        grabbed = _grab_window(window_hint)
        if grabbed is None:
            return {"verified": False, "actual_label": "",
                    "reason": "couldn't re-capture the window to verify — not clicking"}
        img, offset, _rect, _title = grabbed
        pad = int(settings.get("vision.verify_pad_px", 34))
        up = int(settings.get("vision.verify_upscale", 6))
        ix, iy = point[0] - offset[0], point[1] - offset[1]
        x0, y0, x1, y1 = _crop_around_point(img, (ix, iy), pad)

        from PIL import Image
        im = Image.fromarray(img[:, :, ::-1]).crop((x0, y0, x1, y1))
        im = im.resize((max(1, (x1 - x0) * up), max(1, (y1 - y0) * up)))
        import io
        buf = io.BytesIO()
        im.save(buf, format="PNG")

        out = _call_verify_json(buf.getvalue(), approved)
    except Exception as exc:
        return {"verified": False, "actual_label": "",
                "reason": f"couldn't verify the click point ({exc}) — not clicking"}

    if out is None:
        return {"verified": False, "actual_label": "",
                "reason": "couldn't confirm what's at the click point — not clicking"}

    actual = (out.get("actual_label") or "").strip()

    # ---- independent risk cross-check: a benign approval can NEVER be waved
    # through onto a committal/destructive control, whatever the model claims ----
    from jarvis.primitives.input import is_committal_name
    if is_committal_name(actual) and not is_committal_name(approved):
        return {"verified": False, "actual_label": actual,
                "reason": (f"you approved '{approved}', but '{actual}' (a committal "
                           f"action) is at that point — not clicking")}

    # Semantic-agreement override: if the verifier's own label shares a meaningful
    # action token with what was approved ('open folder' vs 'open file'), that IS
    # the same control — accept it even if the model's boolean said otherwise.
    # Measured: without this, the verifier refused clicks it had itself named
    # correctly. It can only ADD allows for labels that agree, and the
    # risk-escalation check above still runs first, so it can't wave through a
    # committal control.
    if not out.get("matches") and not _names_agree(actual, approved):
        return {"verified": False, "actual_label": actual,
                "reason": (f"you approved '{approved}', but '{actual}' is the "
                           f"control at that point — not clicking")}
    return {"verified": True, "actual_label": actual,
            "reason": f"confirmed '{actual}' at the click point"}


# ============================ orchestrator ============================

def locate_and_classify(description: str, window_hint: str | None = None) -> dict:
    """Locate `description` visually and classify its tier. Returns
    {ok, point, label, tier, window_title, confidence} on success, or
    {ok: False, reason} otherwise. Never raises."""
    if not settings.get("vision.enabled", True):
        return {"ok": False, "reason": "vision fallback is disabled"}

    grabbed = _grab_window(window_hint)
    if grabbed is None:
        return {"ok": False, "reason": "couldn't capture the target window"}
    img, offset, rect, title = grabbed

    h, w = img.shape[:2]
    max_edge = int(settings.get("vision.max_edge_px", 1024))
    try:
        png, _scale = _downscale_and_encode(img, max_edge)
    except Exception:
        return {"ok": False, "reason": "couldn't encode the screenshot"}

    raw = _call_gemini(png, description)
    if raw is None:
        return {"ok": False, "reason": "the vision model was unavailable"}

    parsed = _parse_vision_json(raw)
    if parsed is None:
        return {"ok": False, "reason": "vision returned an unreadable response"}
    if not parsed.get("found"):
        return {"ok": False, "reason": f"couldn't find '{description}' on screen, even visually"}

    point = _map_box_to_point(parsed["box"], (w, h), offset)
    if not _point_in_rect(point, rect):
        return {"ok": False, "reason": "vision pointed outside the window bounds — ignored"}

    min_conf = float(settings.get("vision.min_confidence", 0.5))
    tier = _tier_for(parsed["label"], parsed["risk"], parsed["confidence"], min_conf)
    return {"ok": True, "point": point, "label": parsed["label"], "tier": tier,
            "window_title": title, "confidence": parsed["confidence"]}
