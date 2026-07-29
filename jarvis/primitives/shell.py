"""run_shell — arbitrary shell execution (spec §1.2). The highest-risk verb.

Three controls, in order:
  1. DENYLIST (this module): a NARROW, NAMED set of definitively-catastrophic,
     irreversible command shapes that are refused OUTRIGHT — the primitive
     returns tier "blocked" and the executor never even shows an approvable
     modal. This is a BACKSTOP for the "user approves something catastrophic
     they didn't parse" failure mode, NOT a security boundary: it is trivially
     defeated by obfuscation (base64, env expansion, alternate tools). We do
     not claim otherwise — see test_obfuscated_command_not_caught_reaches_confirm.
  2. CONFIRM (existing gate): everything else shows the VERBATIM command and
     waits for explicit approval. This is the primary control.
  3. Execution (run_shell): honest exit-code reporting + a wall-clock timeout
     with process-tree kill.

Anything not on the denylist stays CONFIRM-only, on purpose — occasionally
confirming a safe command beats a broad denylist that breeds false confidence.
"""
from __future__ import annotations

import re
import subprocess

from jarvis import config
from jarvis.core.settings_store import settings

SHELL_LABEL = "cmd.exe"
CLIP = 2000            # max chars kept per stream (payload discipline)
DEFAULT_TIMEOUT_S = 30


def _norm(cmd: str) -> str:
    """Whitespace-collapsed, casefolded copy for matching. This catches
    trivial spacing variants ONLY — not real obfuscation."""
    return " ".join(str(cmd or "").split()).casefold()


# --- denylist rules: (name, predicate, why). Narrow and target-anchored. ---

def _root_recursive_delete(n: str) -> bool:
    # POSIX: rm with BOTH recursive and force, aimed at a root/system path.
    if re.search(r"\brm\b", n) and re.search(r"-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r"
                                             r"|-r\b[^|;&]*-f\b|-f\b[^|;&]*-r\b", n):
        if re.search(r"(^|\s)(/|/\*|~|~/|\$home)(\s|/|\*|$)", n) or \
           re.search(r"(^|\s)/(etc|usr|bin|boot|sys|lib|var|root)(\s|/|$)", n):
            return True
    # Windows: recursive dir delete aimed at a drive root / system tree.
    win_recursive = (re.search(r"\b(del|rd|rmdir)\b", n) and re.search(r"/s\b", n)) \
        or re.search(r"remove-item\b[^|;&]*-recurse", n)
    if win_recursive and re.search(r"(^|\s)[a-z]:\\?(\s|$)"       # bare drive root
                                   r"|%systemroot%"
                                   r"|[a-z]:\\(windows|users|program files)\b", n):
        return True
    return False


def _disk_format_or_wipe(n: str) -> bool:
    if re.search(r"\bformat\b\s+(/\w+\s+)*[a-z]:", n):        # format [flags] X:
        return True
    if "diskpart" in n and "clean" in n:
        return True
    if re.search(r"\b(clear-disk|format-volume|remove-partition)\b", n):
        return True
    if re.search(r"\bcipher\b\s*/w", n):
        return True
    if re.search(r"physicaldrive|of=/dev/sd|of=\\\\\.\\|>\s*/dev/sd", n):
        return True
    return False


def _fork_bomb(n: str) -> bool:
    if re.search(r":\s*\(\s*\)\s*\{[^}]*:\s*\|\s*:[^}]*\}\s*;\s*:", n):
        return True
    if re.search(r"%0\s*\|\s*%0", n):
        return True
    return False


DENYLIST = [
    ("root_recursive_delete", _root_recursive_delete,
     "a recursive force-delete of a system or root path is irreversible"),
    ("disk_format_or_wipe", _disk_format_or_wipe,
     "formatting or wiping a disk/partition destroys everything on it"),
    ("fork_bomb", _fork_bomb,
     "this is a fork bomb — it exists only to hang the machine"),
]


def denylist_match(cmd: str) -> tuple[str, str] | None:
    """(rule_name, why) of the first matching rule, or None."""
    n = _norm(cmd)
    for name, predicate, why in DENYLIST:
        try:
            if predicate(n):
                return name, why
        except Exception:
            continue
    return None


def classify_run_shell(args: dict) -> dict:
    """Dynamic tier for run_shell. Never raises. Returns one of:
      blocked  — kill switch off, empty command, or a denylist hit;
      confirm  — everything else, carrying the VERBATIM command for the modal.
    """
    cmd = str(args.get("command", "") or "").strip()
    if not settings.get("shell.enabled", True):
        return {"tier": "blocked",
                "description": "BLOCKED: shell execution is disabled in settings."}
    if not cmd:
        return {"tier": "blocked", "description": "BLOCKED: no command given."}
    hit = denylist_match(cmd)
    if hit:
        name, why = hit
        return {"tier": "blocked",
                "description": f"BLOCKED ({name}): I won't run that — {why}. "
                               f"Command was: {cmd}"}
    # CONFIRM: the modal shows the verbatim command (via the 'command' field);
    # the description is prose only — NO model summary of what it does.
    cwd = str(config.BASE_DIR)
    return {"tier": "confirm", "command": cmd,
            "description": f"Run a shell command ({SHELL_LABEL}, cwd {cwd}). "
                           f"Review the exact command shown — I cannot verify "
                           f"what it will do."}


def _clip(text: str) -> str:
    text = text or ""
    return text if len(text) <= CLIP else text[:CLIP] + " …[truncated]"


def _kill_tree(pid: int) -> None:
    """Kill the process AND its children — a bare kill leaves the shell's
    grandchildren (e.g. a spawned ping) alive."""
    try:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                       capture_output=True, timeout=10,
                       creationflags=config.NO_WINDOW)
    except Exception:
        pass


def _timeout_s() -> float:
    try:
        return max(0.2, float(settings.get("shell.timeout_s", DEFAULT_TIMEOUT_S)))
    except Exception:
        return float(DEFAULT_TIMEOUT_S)


def run_shell(command: str) -> dict:
    """Run one cmd.exe command and report HONESTLY. exit 0 is the only success;
    any non-zero exit is a failure even with stdout. A command that outlives
    the timeout has its whole process tree killed. Never raises.

    Returns {ok, exit_code, stdout, stderr, message}."""
    command = str(command or "").strip()
    if not command:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "",
                "message": "no command given."}
    timeout = _timeout_s()
    try:
        proc = subprocess.Popen(
            command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, errors="replace",
            creationflags=config.NO_WINDOW)  # output is captured, so no window
    except Exception as exc:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "",
                "message": f"couldn't start the command: {exc}"}

    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc.pid)
        try:
            out, err = proc.communicate(timeout=5)
        except Exception:
            out, err = "", ""

    code = proc.returncode
    out, err = _clip(out or ""), _clip(err or "")

    def _tail() -> str:
        bits = []
        if out.strip():
            bits.append(f"stdout: {out.strip()}")
        if err.strip():
            bits.append(f"stderr: {err.strip()}")
        return (" " + " ".join(bits)) if bits else ""

    if timed_out:
        return {"ok": False, "exit_code": code, "stdout": out, "stderr": err,
                "message": f"timed out after {timeout:g}s (process tree killed)."
                           + _tail()}

    ok = code == 0
    return {"ok": ok, "exit_code": code, "stdout": out, "stderr": err,
            "message": f"exit {code}." + _tail()}
