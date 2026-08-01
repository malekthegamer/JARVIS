"""Stage-2 exit tests: text -> brain -> text, with the state machine and the
never-crash contract. (a) fake provider drives the THINKING->IDLE sequence;
(b) live Gemini ping; (c) a timeout-raising provider still restores IDLE and
returns a friendly message, never an exception."""
from __future__ import annotations

import pytest

from jarvis.core.errors import ProviderError
from jarvis.providers.brain.base import BrainProvider, BrainResponse
from jarvis.state import AgentState, broadcaster


class FakeProvider(BrainProvider):
    supports_tools = True

    def __init__(self, reply="All systems nominal, sir."):
        self.reply = reply
        self.calls: list[dict] = []

    def is_configured(self):
        return True

    def generate(self, messages, system_prompt, tools=None):
        self.calls.append({"messages": list(messages), "tools": tools})
        return BrainResponse(text=self.reply)


class TimeoutProvider(BrainProvider):
    def is_configured(self):
        return True

    def generate(self, messages, system_prompt, tools=None):
        raise ProviderError("connection", "Gemini", "request timed out after 30s")


@pytest.fixture()
def state_log():
    events: list[dict] = []
    unsubscribe = broadcaster.subscribe(events.append)
    yield events
    unsubscribe()


def _make_brain(provider):
    from jarvis.brain import JarvisBrain
    brain = JarvisBrain()
    brain._provider_override = provider
    return brain



@pytest.fixture(autouse=True)
def _restore_brain_settings():
    """LEAK GUARD. The chain tests point brain.models.gemini at fake names like
    "m-primary"; without restoring, every LIVE test later in the same pytest
    process calls a nonexistent model and 404s. That is exactly what happened —
    a gate run showed 15 failures with ZERO rate-limit errors, and the cause was
    this file, not the product. (Same class as the slice-43 test that leaked
    web.allow_actions.)"""
    from jarvis.core.settings_store import settings
    keys = ("brain.models.gemini", "brain.fallback_models", "brain.active")
    saved = {k: settings.get(k) for k in keys}
    yield
    for k, v in saved.items():
        settings.set(k, v, persist=False)

def test_fake_provider_reply_and_state_sequence(state_log):
    fake = FakeProvider()
    brain = _make_brain(fake)
    reply = brain.think("status report")

    assert reply == "All systems nominal, sir."
    states = [e["state"] for e in state_log]
    assert states == [AgentState.THINKING.value, AgentState.IDLE.value]
    assert broadcaster.current is AgentState.IDLE
    # history holds the full exchange
    assert brain.history[-2:] == [
        {"role": "user", "content": "status report"},
        {"role": "assistant", "content": "All systems nominal, sir."},
    ]
    # slice 2+: the primitive schemas reach the provider
    tool_names = [t["name"] for t in fake.calls[0]["tools"]]
    assert "launch_app" in tool_names


def test_timeout_returns_friendly_and_restores_idle(state_log):
    brain = _make_brain(TimeoutProvider())
    reply = brain.think("hello?")

    assert "Couldn't reach Gemini" in reply  # friendly text, not a traceback
    assert broadcaster.current is AgentState.IDLE
    assert state_log[-1]["state"] == AgentState.IDLE.value


def test_unexpected_exception_never_escapes(state_log):
    class ExplodingProvider(BrainProvider):
        def is_configured(self):
            return True

        def generate(self, messages, system_prompt, tools=None):
            raise RuntimeError("kaboom")

    brain = _make_brain(ExplodingProvider())
    reply = brain.think("hi")
    assert "kaboom" in reply or "wrong" in reply.lower()
    assert broadcaster.current is AgentState.IDLE


def test_history_trims_to_limit():
    fake = FakeProvider(reply="ok")
    brain = _make_brain(fake)
    for i in range(60):
        brain.think(f"message {i}")
    from jarvis.core.settings_store import settings
    limit = int(settings.get("history_max_messages", 40))
    assert len(brain.history) <= limit
    assert brain.history[0]["role"] == "user"  # never starts mid-exchange


# ---------- slice 10: long-term memory integration ----------

from jarvis.providers.brain.base import ToolCall


class CapturingProvider(BrainProvider):
    """Records the system_prompt it was handed; returns plain text."""
    supports_tools = True

    def __init__(self, reply="Noted, sir."):
        self.reply = reply
        self.system_prompts: list[str] = []

    def is_configured(self):
        return True

    def generate(self, messages, system_prompt, tools=None):
        self.system_prompts.append(system_prompt)
        return BrainResponse(text=self.reply)


class ScriptedToolProvider(BrainProvider):
    """Round 1: emit the given tool call. Round 2: final prose."""
    supports_tools = True

    def __init__(self, name, args):
        self.tc = ToolCall(id="m1", name=name, args=args)
        self.rounds = 0
        self.tool_results: list[str] = []

    def is_configured(self):
        return True

    def generate(self, messages, system_prompt, tools=None):
        self.rounds += 1
        if self.rounds == 1:
            return BrainResponse(tool_calls=[self.tc])
        self.tool_results = [m["content"] for m in messages if m["role"] == "tool"]
        return BrainResponse(text="Done, sir.")


@pytest.fixture()
def mem_brain(tmp_path):
    from jarvis.core.memory import MemoryStore
    brain = _make_brain(CapturingProvider())
    brain.memory = MemoryStore(tmp_path / "mem.bin")
    return brain


def test_relevant_memory_injected_into_system_prompt(mem_brain):
    mem_brain.memory.add("I am allergic to peanuts")
    mem_brain.think("what am I allergic to?")
    prompt = mem_brain.provider().system_prompts[-1]
    assert "peanuts" in prompt
    assert "REMEMBER ABOUT THE USER" in prompt


def test_unrelated_query_no_memory_block(mem_brain):
    mem_brain.memory.add("I am allergic to peanuts")
    mem_brain.think("what is the capital of France?")
    prompt = mem_brain.provider().system_prompts[-1]
    assert "peanuts" not in prompt
    assert "REMEMBER ABOUT THE USER" not in prompt


def test_remember_tool_persists(tmp_path):
    from jarvis.core.memory import MemoryStore
    store = MemoryStore(tmp_path / "mem.bin")
    brain = _make_brain(ScriptedToolProvider("remember",
                                             {"text": "I take my coffee black"}))
    brain.memory = store
    brain.think("remember that I take my coffee black")
    assert any("coffee black" in r["text"] for r in store.all())
    # and it survives a restart (fresh instance, same file)
    assert any("coffee black" in r["text"]
               for r in MemoryStore(tmp_path / "mem.bin").all())


def test_recall_tool_lists(tmp_path):
    from jarvis.core.memory import MemoryStore
    store = MemoryStore(tmp_path / "mem.bin")
    store.add("I am allergic to peanuts")
    store.add("my car is a blue Civic")
    brain = _make_brain(ScriptedToolProvider("recall", {}))
    brain.memory = store
    brain.think("what do you remember about me?")
    listing = " ".join(brain.provider().tool_results)
    assert "peanuts" in listing and "Civic" in listing


def test_forget_tool_removes_with_readback(tmp_path):
    from jarvis.core.memory import MemoryStore
    store = MemoryStore(tmp_path / "mem.bin")
    store.add("I am allergic to peanuts")
    store.add("my flight is on Tuesday")
    brain = _make_brain(ScriptedToolProvider("forget", {"query": "peanuts"}))
    brain.memory = store
    brain.think("forget that I'm allergic to peanuts")
    assert not any("peanuts" in r["text"] for r in store.all())
    assert any("peanuts" in tr for tr in brain.provider().tool_results)  # readback


def test_forget_tool_ambiguous_deletes_nothing(tmp_path):
    from jarvis.core.memory import MemoryStore
    store = MemoryStore(tmp_path / "mem.bin")
    store.add("I take my coffee black")
    store.add("I drink my coffee at 8am")
    brain = _make_brain(ScriptedToolProvider("forget", {"query": "coffee"}))
    brain.memory = store
    brain.think("forget my coffee thing")
    assert len(store.all()) == 2, "ambiguous forget must delete nothing"
    result = " ".join(brain.provider().tool_results).lower()
    assert "which" in result or "match" in result  # asks the user to disambiguate


def test_memory_error_does_not_break_think(mem_brain, monkeypatch):
    """A memory-subsystem failure must never break the reply loop."""
    monkeypatch.setattr(mem_brain.memory, "retrieve",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    reply = mem_brain.think("hello there")
    assert reply and "boom" not in reply  # degraded to no-memory, still answered


def test_live_gemini_ping():
    """The real thing: one live round-trip through the actual provider."""
    from jarvis import config
    if not config.get_api_key("gemini"):
        pytest.skip("GEMINI_API_KEY not configured")
    from jarvis.brain import JarvisBrain
    brain = JarvisBrain()
    reply = brain.think("What is 2+2? Reply with just the number.")
    assert reply, "empty reply from live Gemini"
    assert "isn't configured" not in reply and "hit a problem" not in reply, reply
    assert "4" in reply, reply


# ---------------- pinned always-on preferences (slice 19 Stage 3) ----------

def test_pinned_block_in_every_prompt(mem_brain):
    mem_brain.memory.add("always address me as Captain", pinned=True)
    mem_brain.think("what is the capital of France?")   # totally unrelated
    prompt = mem_brain.provider().system_prompts[-1]
    assert "Captain" in prompt and "STANDING PREFERENCES" in prompt


def test_pinned_and_relevance_blocks_coexist(mem_brain):
    mem_brain.memory.add("always address me as Captain", pinned=True)
    mem_brain.memory.add("I am allergic to peanuts")
    mem_brain.think("what am I allergic to?")
    prompt = mem_brain.provider().system_prompts[-1]
    assert "Captain" in prompt and "STANDING PREFERENCES" in prompt
    assert "peanuts" in prompt and "REMEMBER ABOUT THE USER" in prompt
    assert "volunteer" in prompt.lower()   # slice-10 framing stays verbatim


def test_remember_pinned_tool_roundtrip(tmp_path):
    from jarvis.core.memory import MemoryStore
    store = MemoryStore(tmp_path / "mem.bin")
    brain = _make_brain(ScriptedToolProvider(
        "remember", {"text": "always address me as Captain", "pinned": True}))
    brain.memory = store
    brain.think("from now on, always address me as Captain")
    recs = store.all()
    assert recs and recs[0].get("pinned") is True
    assert any("pinned" in tr.lower() for tr in brain.provider().tool_results)


def test_recall_marks_pinned(tmp_path):
    from jarvis.core.memory import MemoryStore
    store = MemoryStore(tmp_path / "mem.bin")
    store.add("always address me as Captain", pinned=True)
    store.add("I am allergic to peanuts")
    brain = _make_brain(ScriptedToolProvider("recall", {}))
    brain.memory = store
    brain.think("what do you remember?")
    listing = " ".join(brain.provider().tool_results)
    assert "[pinned]" in listing and "Captain" in listing


# ---------- slice 44: brain resilience (a 429 stops being a failure) ----------
#
# WHY. Free-tier quota produced 6-9 false failures in EVERY gate for six slices.
# The cost is lost signal, not inconvenience: in gate 43 a real regression I had
# just caused ("never read the page", after I changed browse_navigate's
# description) was indistinguishable from quota noise. I caught it that time.
#
# STAGE-0 MEASUREMENTS this is built on:
#   * the primary caps at ~15 RPM (429 at burst 15)
#   * gemini-2.5-flash ANSWERED while the primary was 429 -> SEPARATE buckets,
#     so a model-level chain genuinely works
#   * both primary and gemini-2.5-flash returned a correct tool_call with all 40
#     real tool declarations; gemini-2.0-flash could NOT be proven, so it is not
#     in the default chain

from jarvis.core.errors import ProviderError
from jarvis.providers.brain.base import BrainProvider, BrainResponse


class _ScriptedBrain(BrainProvider):
    """A brain whose per-model behaviour is scripted, so chain-walking is
    deterministic. `plan` maps model name -> either a ProviderError to raise or
    the text to answer with."""
    name = "scripted"
    supports_tools = True

    def __init__(self, plan: dict):
        self.plan = plan
        self.calls: list[str] = []

    def is_configured(self) -> bool:
        return True

    def generate(self, messages, system_prompt, tools=None, model=None):
        self.calls.append(model)
        outcome = self.plan.get(model, "default")
        if isinstance(outcome, Exception):
            raise outcome
        return BrainResponse(text=f"{outcome} from {model}")


def _brain_with(monkeypatch, plan, chain=("m-primary", "m-backup")):
    from jarvis.brain import JarvisBrain
    from jarvis.core.settings_store import settings
    settings.set("brain.models.gemini", chain[0], persist=False)
    settings.set("brain.fallback_models", list(chain[1:]), persist=False)
    b = JarvisBrain()
    prov = _ScriptedBrain(plan)
    b._provider_override = prov
    return b, prov


# ---- Stage 1: an explicit model argument ----

def test_generate_accepts_an_explicit_model():
    """The chain needs to ask for a SPECIFIC model; reading a global setting per
    call cannot express 'try the next one'."""
    import inspect
    sig = inspect.signature(BrainProvider.generate)
    assert "model" in sig.parameters, \
        "brain providers must accept an explicit model for the chain to work"


def test_gemini_uses_the_explicit_model_over_the_setting(monkeypatch):
    from jarvis.core.settings_store import settings
    from jarvis.providers.brain import gemini_provider as gp
    settings.set("brain.models.gemini", "from-setting", persist=False)
    seen = {}

    class _FakeModels:
        def generate_content(self, model=None, contents=None, config=None):
            seen["model"] = model
            raise RuntimeError("stop here — we only care which model was asked for")

    class _FakeClient:
        models = _FakeModels()

    prov = gp.GeminiProvider() if hasattr(gp, "GeminiProvider") else None
    if prov is None:
        import pytest
        pytest.skip  # noqa
    monkeypatch.setattr(prov, "_client", lambda: _FakeClient())
    try:
        prov.generate([{"role": "user", "content": "hi"}], "sys", model="explicit-model")
    except Exception:
        pass
    assert seen.get("model") == "explicit-model", seen


def test_gemini_falls_back_to_the_setting_when_no_model_given(monkeypatch):
    from jarvis.core.settings_store import settings
    from jarvis.providers.brain import gemini_provider as gp
    settings.set("brain.models.gemini", "from-setting", persist=False)
    seen = {}

    class _FakeModels:
        def generate_content(self, model=None, contents=None, config=None):
            seen["model"] = model
            raise RuntimeError("stop")

    class _FakeClient:
        models = _FakeModels()

    prov = gp.GeminiProvider()
    monkeypatch.setattr(prov, "_client", lambda: _FakeClient())
    try:
        prov.generate([{"role": "user", "content": "hi"}], "sys")
    except Exception:
        pass
    assert seen.get("model") == "from-setting", seen


# ---- Stage 2: the bounded chain ----

def _transient(kind):
    return ProviderError(kind, "scripted", "simulated")


def test_rate_limit_walks_to_the_next_model(monkeypatch):
    b, prov = _brain_with(monkeypatch, {"m-primary": _transient("rate_limit")})
    resp = b._generate_with_fallback("sys", None)
    assert prov.calls == ["m-primary", "m-backup"], prov.calls
    assert "m-backup" in resp.text


def test_quota_exceeded_walks_to_the_next_model(monkeypatch):
    b, prov = _brain_with(monkeypatch, {"m-primary": _transient("quota_exceeded")})
    b._generate_with_fallback("sys", None)
    assert prov.calls == ["m-primary", "m-backup"], prov.calls


def test_connection_error_walks_to_the_next_model(monkeypatch):
    b, prov = _brain_with(monkeypatch, {"m-primary": _transient("connection")})
    b._generate_with_fallback("sys", None)
    assert prov.calls == ["m-primary", "m-backup"], prov.calls


def test_missing_key_does_NOT_walk_the_chain(monkeypatch):
    """Masking a real error is worse than failing. A bad key is not transient —
    walking the chain would hide a configuration problem behind a slower answer."""
    import pytest
    b, prov = _brain_with(monkeypatch, {"m-primary": _transient("missing_key")})
    with pytest.raises(ProviderError) as exc:
        b._generate_with_fallback("sys", None)
    assert exc.value.kind == "missing_key"
    assert prov.calls == ["m-primary"], f"must not try the backup: {prov.calls}"


def test_bad_response_does_NOT_walk_the_chain(monkeypatch):
    import pytest
    b, prov = _brain_with(monkeypatch, {"m-primary": _transient("bad_response")})
    with pytest.raises(ProviderError):
        b._generate_with_fallback("sys", None)
    assert prov.calls == ["m-primary"], prov.calls


def test_each_model_is_tried_at_most_once(monkeypatch):
    """Bounded: no retry spiral. Every candidate fails transiently here."""
    import pytest
    b, prov = _brain_with(monkeypatch,
                          {"m-primary": _transient("rate_limit"),
                           "m-backup": _transient("rate_limit")},
                          chain=("m-primary", "m-backup", "m-third"))
    b_plan = {"m-primary": _transient("rate_limit"),
              "m-backup": _transient("rate_limit"),
              "m-third": _transient("rate_limit")}
    prov.plan = b_plan
    with pytest.raises(ProviderError):
        b._generate_with_fallback("sys", None)
    assert prov.calls == ["m-primary", "m-backup", "m-third"], prov.calls
    assert len(prov.calls) == len(set(prov.calls)), "a model was retried"


def test_exhausted_chain_reports_the_original_error_honestly(monkeypatch):
    """When everything is rate-limited the user must get the honest rate-limit
    message, not a generic 'something went wrong'."""
    import pytest
    b, prov = _brain_with(monkeypatch,
                          {"m-primary": _transient("rate_limit"),
                           "m-backup": _transient("rate_limit")})
    with pytest.raises(ProviderError) as exc:
        b._generate_with_fallback("sys", None)
    assert exc.value.kind == "rate_limit"


def test_duplicate_chain_entries_are_collapsed(monkeypatch):
    """A misconfigured chain listing the primary again must not double-call it."""
    b, prov = _brain_with(monkeypatch, {"m-primary": _transient("rate_limit")},
                          chain=("m-primary", "m-primary", "m-backup"))
    b._generate_with_fallback("sys", None)
    assert prov.calls == ["m-primary", "m-backup"], prov.calls


# ---- Stage 3: attribution ----

def test_the_answering_model_is_recorded_for_attribution(monkeypatch):
    """A weaker brain must never answer SILENTLY — uptime bought by quietly
    downgrading the model is a correctness risk disguised as reliability."""
    b, _prov = _brain_with(monkeypatch, {"m-primary": _transient("rate_limit")})
    b._generate_with_fallback("sys", None)
    assert b.last_model == "m-backup", b.last_model
    assert b.last_model_was_fallback is True


def test_primary_success_is_not_labelled_as_a_fallback(monkeypatch):
    b, _prov = _brain_with(monkeypatch, {})
    b._generate_with_fallback("sys", None)
    assert b.last_model == "m-primary"
    assert b.last_model_was_fallback is False


def test_fallback_is_surfaced_to_the_hud(monkeypatch):
    """The HUD must be able to SEE a downgrade. Attribution kept only in a log
    nobody reads would be attribution in name only."""
    from jarvis import server
    from jarvis.brain import jarvis_brain
    prev, prev_flag = jarvis_brain.last_model, jarvis_brain.last_model_was_fallback
    try:
        jarvis_brain.last_model = "gemini-2.5-flash"
        jarvis_brain.last_model_was_fallback = True
        ev = server._sample_telemetry()
        assert ev.get("brain_model") == "gemini-2.5-flash", ev
        assert ev.get("brain_is_fallback") is True, ev

        jarvis_brain.last_model_was_fallback = False
        ev = server._sample_telemetry()
        assert ev.get("brain_is_fallback") is False
    finally:
        jarvis_brain.last_model, jarvis_brain.last_model_was_fallback = prev, prev_flag


# ---- Slice 51: the chain order has ONE definition (shared with vision) ----

def test_brain_and_vision_share_one_model_chain():
    """Two copies of the chain would fork the config contract: the same
    brain.fallback_models setting could silently mean different things in
    brain.py and vision.py. One definition, both import it."""
    from jarvis.brain import JarvisBrain
    from jarvis.core.model_chain import model_chain
    from jarvis.core.settings_store import settings

    settings.set("brain.models.gemini", "m-primary", persist=False)
    settings.set("brain.fallback_models", ["m-backup", "m-third"], persist=False)

    assert model_chain() == ["m-primary", "m-backup", "m-third"]
    assert JarvisBrain()._model_chain() == model_chain(), \
        "brain must delegate to the shared chain, not keep its own copy"


def test_shared_chain_dedupes_and_preserves_order():
    """A fallback list that repeats the active model must not make us call the
    same model twice — that burns quota on a bucket we already know is dry."""
    from jarvis.core.model_chain import model_chain
    from jarvis.core.settings_store import settings

    settings.set("brain.models.gemini", "m-primary", persist=False)
    settings.set("brain.fallback_models", ["m-primary", "m-backup", "m-backup"],
                 persist=False)
    assert model_chain() == ["m-primary", "m-backup"]


def test_shared_chain_is_just_the_active_model_when_no_fallbacks():
    """An empty fallback list must yield today's exact behaviour: one model,
    one call, no retry."""
    from jarvis.core.model_chain import model_chain
    from jarvis.core.settings_store import settings

    settings.set("brain.models.gemini", "m-only", persist=False)
    settings.set("brain.fallback_models", [], persist=False)
    assert model_chain() == ["m-only"]


# ---- Slice 52: the fallback model must have real headroom ----
# MEASURED 2026-08-01 by bursting each candidate and READING the 429's quotaId
# (scratchpad/probe_fallback_candidates.py). All three passed tool calling
# against the real 40-tool schema AND vision Q&A, so capability was never the
# discriminator -- the quota AXIS was:
#
#   gemini-2.5-flash       DAILY ceiling 20      <- incumbent; tripped its DAY
#                                                   limit before its minute one
#   gemini-2.5-flash-lite  per-minute 10         <- daily bucket intact, but 10
#                                                   RPM is BELOW the pacer budget
#   gemini-3.5-flash-lite  per-minute 15         <- daily intact, RPM matches the
#                                                   primary; CHOSEN
#
# "Daily intact" means the burst hit a PerMinute quotaId, i.e. the daily ceiling
# is above the burst size (24) -- NOT that it is unlimited. The exact daily cap
# for the lite models is unmeasured; strictly-better-than-20 is the claim.

MEASURED_RPM_CAPS = {
    "gemini-3.1-flash-lite": 15,
    "gemini-3.5-flash-lite": 15,
    "gemini-2.5-flash-lite": 10,
    "gemini-2.5-flash": 15,
}


def test_default_fallback_model_is_not_the_daily_capped_one():
    """A fallback exists for exhaustion. One whose OWN daily ceiling trips first
    is decoration -- slice 51 proved gemini-2.5-flash dies after 20 requests."""
    from jarvis.core.settings_store import DEFAULT_SETTINGS

    chain = DEFAULT_SETTINGS["brain"]["fallback_models"]
    assert "gemini-2.5-flash" not in chain, (
        "gemini-2.5-flash has a DAILY ceiling of 20 requests (measured slice 51) "
        "— it cannot rescue an exhausted primary")
    assert chain == ["gemini-3.5-flash-lite"], chain


def test_pacer_budget_fits_every_model_in_the_default_chain():
    """THE REASON gemini-2.5-flash-lite was rejected despite passing capability.

    The pacer paces every model at ONE budget. A chain member whose real RPM cap
    is BELOW that budget gets paced too fast, 429s, and manufactures exactly the
    false failures slice 45 existed to remove. So the invariant is mechanical:
    every model we ship in the chain must tolerate the pacer's rate."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _pacer import DEFAULT_BUDGET_PER_MIN

    from jarvis.core.settings_store import DEFAULT_SETTINGS

    active = DEFAULT_SETTINGS["brain"]["models"]["gemini"]
    for model in [active] + DEFAULT_SETTINGS["brain"]["fallback_models"]:
        cap = MEASURED_RPM_CAPS.get(model)
        assert cap is not None, (
            f"{model} is in the shipped chain but its RPM cap was never "
            f"measured — measure it before shipping it")
        assert cap >= DEFAULT_BUDGET_PER_MIN, (
            f"{model} caps at {cap} RPM but the test pacer runs at "
            f"{DEFAULT_BUDGET_PER_MIN}/min — it would 429 under pacing and "
            f"forge failures")


def test_the_chain_actually_changes_model_between_attempts():
    """Guard against a chain that 'falls back' to the same bucket it just
    exhausted — that would retry a bucket already known to be dry."""
    from jarvis.core.settings_store import DEFAULT_SETTINGS

    active = DEFAULT_SETTINGS["brain"]["models"]["gemini"]
    assert active not in DEFAULT_SETTINGS["brain"]["fallback_models"], \
        "the fallback list must not contain the active model"


# ============ slice 57 stage 0: the prompt tells the truth, history survives ============

def test_stale_capability_claims_are_not_in_the_prompt():
    """THE BUG: the prompt said 'Abilities not yet wired up (file access outside
    your workspace…)' — while the SAME prompt, ~70 lines earlier, correctly said
    JARVIS can work with the user's real files anywhere on the PC. Slices 32-33
    shipped that access and this clause was never updated, so the model was being
    instructed to REFUSE a capability it has. Directly causes 'it fails or
    refuses things it should be able to do'."""
    from jarvis.brain import BASE_SYSTEM_PROMPT

    lowered = BASE_SYSTEM_PROMPT.lower()
    assert "file access outside your workspace" not in lowered, \
        "the prompt still claims real-filesystem access is unwired (slices 32-33 shipped it)"
    # The capability it DOES have must still be advertised.
    assert "real files anywhere on the pc" in lowered


def test_the_prompt_still_admits_what_is_genuinely_unwired():
    """The fix must not overcorrect into claiming abilities it lacks — calendars
    and inbox reading really are not built."""
    from jarvis.brain import BASE_SYSTEM_PROMPT

    lowered = BASE_SYSTEM_PROMPT.lower()
    assert "calendar" in lowered and "inbox" in lowered, \
        "JARVIS must still say it cannot do calendars / inbox reading"


# ---- history: a long tool chain must not evict the conversation ----

def _exchange(n):
    return [{"role": "user", "content": f"user message {n}"},
            {"role": "assistant", "content": f"assistant reply {n}"}]


def _tool_round(i):
    """One round as brain.py records it: an assistant tool-call turn plus its
    tool result (brain.py:421-443)."""
    return [{"role": "assistant", "content": "",
             "tool_calls": [{"id": f"c{i}", "name": "read_ui_tree", "args": {}}]},
            {"role": "tool", "tool_call_id": f"c{i}", "name": "read_ui_tree",
             "content": "OK: some ui"}]


def test_a_long_tool_chain_does_not_evict_the_conversation():
    """THE BUG: history_max_messages=40 counts TOOL turns, and one 12-round chain
    records ~24 of them. A flat tail-slice therefore threw away everything the
    user had actually been talking about — JARVIS forgot the conversation after
    one complex task."""
    from jarvis.brain import JarvisBrain

    b = JarvisBrain()
    for n in range(4):
        b.history.extend(_exchange(n))
    for i in range(20):                      # 40 tool messages — a big chain
        b.history.extend(_tool_round(i))
    b.history.append({"role": "user", "content": "what were we talking about?"})

    b._trim()

    said = " ".join(m.get("content") or "" for m in b.history
                    if m["role"] == "user")
    assert "user message 0" in said or "user message 1" in said, \
        f"the conversation was evicted by tool turns; users left: {said!r}"
    assert "what were we talking about?" in said


def test_trim_never_orphans_a_tool_result_from_its_tool_call():
    """COHERENCE. Gemini 400s if a tool result appears with no preceding
    tool_call that owns it, so trimming must drop the pair together — an
    orphaned result is worse than a forgotten one."""
    from jarvis.brain import JarvisBrain

    b = JarvisBrain()
    for n in range(3):
        b.history.extend(_exchange(n))
    for i in range(25):
        b.history.extend(_tool_round(i))

    b._trim()

    offered = set()
    for m in b.history:
        if m["role"] == "assistant":
            for tc in m.get("tool_calls") or []:
                offered.add(tc["id"])
        elif m["role"] == "tool":
            assert m["tool_call_id"] in offered, \
                f"orphaned tool result {m['tool_call_id']} — no preceding tool_call"


def test_trim_still_starts_on_a_user_message_and_respects_the_cap():
    """The two properties the old implementation did guarantee must survive."""
    from jarvis.core.settings_store import settings
    from jarvis.brain import JarvisBrain

    limit = int(settings.get("history_max_messages", 40))
    b = JarvisBrain()
    for n in range(30):
        b.history.extend(_exchange(n))

    b._trim()

    assert len(b.history) <= limit, f"cap breached: {len(b.history)} > {limit}"
    assert b.history[0]["role"] == "user", b.history[0]


# ==================== slice 57 stage 4: the film-JARVIS voice ====================
# Persona was ONE adjective ("witty") against ~90 lines of tooling rules, with no
# tone, length or variation guidance -- so replies came out as generic-assistant
# prose and acknowledgements repeated verbatim.
#
# This is ALSO a latency fix, which is why it earns budget in a 4-day plan:
# stage 1 measured a four-sentence reply taking ~17 SECONDS to speak aloud.
# Every sentence removed is worth far more than any millisecond saved in
# synthesis, so brevity is a performance requirement, not a style preference.

def test_the_persona_specifies_brevity_and_not_just_a_mood():
    from jarvis.brain import BASE_SYSTEM_PROMPT

    lowered = BASE_SYSTEM_PROMPT.lower()
    assert "one sentence is your default" in lowered, \
        "the prompt must state a concrete length target, not just 'be concise'"
    assert "vary your acknowledgements" in lowered, \
        "without this the model repeats the same opener every turn"


def test_the_old_hedged_persona_lines_are_gone():
    """'witty' asserted once and 'sir occasionally' produced neither wit nor
    the character — the film's JARVIS says 'sir' habitually."""
    from jarvis.brain import BASE_SYSTEM_PROMPT

    lowered = BASE_SYSTEM_PROMPT.lower()
    assert "a witty and highly capable" not in lowered
    assert "'sir' occasionally" not in lowered


def test_humour_is_specified_as_restraint_not_as_jokes():
    """The failure mode of 'be funny' in a system prompt is a model that makes
    puns. The character is dry, not comic."""
    from jarvis.brain import BASE_SYSTEM_PROMPT

    lowered = BASE_SYSTEM_PROMPT.lower()
    assert "understatement" in lowered
    assert "never enthusiastic" in lowered


def test_live_replies_are_actually_short():
    """THE MEASUREMENT, not the intention. A prompt that asks for brevity and
    does not get it is worthless, and the cost is paid in seconds of speech.

    Simple questions must come back in roughly one sentence. The bar is
    deliberately generous (<= 30 words) so this fails only on real regression,
    not on a model having a slightly wordy day."""
    from jarvis import config
    from jarvis.brain import JarvisBrain

    if not config.get_api_key("gemini"):
        pytest.skip("GEMINI_API_KEY not configured")

    prompts = [
        "what is the capital of France?",
        "set the volume to 40 percent",
        "what is 17 times 3?",
    ]
    lengths = []
    for p in prompts:
        b = JarvisBrain()          # fresh history: no cross-talk between prompts
        reply = b.think(p)
        lengths.append(len(reply.split()))

    worst = max(lengths)
    assert worst <= 30, \
        f"replies are too long to speak comfortably: {worst} words, all={lengths}"
