"""Slice 44 live proof — force a REAL 429 and show the chain answering.

The deterministic tests use scripted providers, which proves the walking logic
but not that it works against the actual API. This burns the primary's quota on
purpose, then asks JARVIS a real question and checks that the answer came from
the FALLBACK model and is attributed.

    python tests/harness_brain_chain.py

Costs a burst of ~15 cheap calls against the primary's per-minute cap. Stage-0
measurements this relies on: the primary caps at ~15 RPM, and gemini-2.5-flash
has a SEPARATE bucket (it answered while the primary was 429).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis import config                          # noqa: E402
from jarvis.core.settings_store import settings     # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def main() -> int:
    primary = settings.get("brain.models.gemini", "gemini-3.1-flash-lite")
    chain = settings.get("brain.fallback_models", [])
    print(f"primary  : {primary}")
    print(f"fallbacks: {chain}\n")
    if not chain:
        print("no fallback configured — nothing to prove")
        return 2

    from google import genai
    client = genai.Client(api_key=config.get_api_key("gemini"))

    # Bursting the primary is NOT deterministic: the RPM window slides, so the
    # primary recovers within a second or two and the chain never fires (the
    # first version of this harness "passed" the answer check while proving
    # nothing about the fallback). Instead point the primary at a model whose
    # quota is genuinely exhausted — a real, repeatable 429 — and keep the
    # PROVEN fallback behind it.
    exhausted = "gemini-2.0-flash"
    print(f"pointing the primary at {exhausted} (quota exhausted -> a real 429)")
    try:
        client.models.generate_content(model=exhausted, contents="hi")
        print(f"  {exhausted} answered — its quota has reset, so it cannot be")
        print("  used to force a failure. Re-run later.")
        return 2
    except Exception as exc:
        low = str(exc).lower()
        if not ("429" in low or "resource_exhausted" in low):
            print(f"  {exhausted} failed for a NON-quota reason: {str(exc)[:70]}")
            return 2
        print("  confirmed: it 429s")

    prev_primary = settings.get("brain.models.gemini")
    settings.set("brain.models.gemini", exhausted, persist=False)

    # ---- the real question, through the real brain ------------------------
    from jarvis.brain import JarvisBrain
    brain = JarvisBrain()
    t0 = time.time()
    reply = brain.think("In one short sentence, what is 2 plus 2?")
    took = time.time() - t0

    print(f"\nreply ({took:.1f}s): {reply[:100]!r}")
    check("answered_despite_the_primary_being_rate_limited",
          bool(reply) and "rate-limiting" not in reply.lower(), reply[:70])
    check("answer_came_from_the_fallback_model",
          brain.last_model in chain, f"last_model={brain.last_model!r}")
    check("fallback_is_attributed_not_silent",
          brain.last_model_was_fallback is True,
          f"was_fallback={brain.last_model_was_fallback}")

    print(f"\n{'ALL CHECKS PASSED' if not FAILURES else 'FAILURES: ' + ', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
