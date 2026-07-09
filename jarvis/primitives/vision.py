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

KNOWN LIMITATION — confabulation: gemini-3.1-flash-lite will invent a plausible
control (with confidence 1.0) when asked to find something that ISN'T in the
image; prompt engineering does not reliably stop this (verified 2026-07). So a
found:false result is NOT guaranteed for a genuinely-absent target. The
defenses are therefore downstream, not here: (1) the CONFIRM gate — a
confabulated destructive/committal action still shows the user its label and
waits for approval; (2) the execution-time from_point hit-test in
input.click(point=). Confidence thresholding is useless against this (the model
is overconfident), so min_confidence only guards the honest low-confidence case.
"""
from __future__ import annotations

import json

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
    from jarvis.primitives.input import _DESTRUCTIVE_RE  # one classifier, two sources

    risk = (risk or "").lower()
    if risk in ("destructive", "committal"):
        return "confirm"
    if risk == "unsure":
        return "confirm"
    if confidence is None or confidence < min_confidence:
        return "confirm"  # fail closed on uncertainty — never click an unknown control
    if _DESTRUCTIVE_RE.search((label or "").lower()):
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
    from jarvis.primitives.input import _target_window
    win, title = _target_window(window_hint)
    if win is None:
        return None
    try:
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
