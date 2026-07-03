#!/usr/bin/env python3
"""subscription.py — turn a scoperoute run's notional $ into "% of your Claude plan",
and pull your real tier + current usage/spend from CodexBar when it's available.

The launch prompt: "…ask what my Claude subscription is (Pro/Max/Teams) to calculate my
spending estimate in $/% from subscription, and tell me what to run first." This module
supplies the math + the (optional) real data; the skill does the asking when CodexBar
can't detect the tier.

CodexBar (https://github.com/konon4/CodexBar — a public fork of steipete/CodexBar) exposes
a `codexbar` CLI:
  - `codexbar cost  --provider claude --json-only`  → token counts + $ to date  (NO auth)
  - `codexbar usage --provider claude --json-only`  → tier + per-window quota %  (needs auth)
We auto-discover the binary, health-check it (the globally-installed one can be config-broken;
a freshly-built fork CLI still works), redact identities, and degrade to None so the skill
falls back to asking the tier.

Stdlib only.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

# Notional monthly price per tier, USD. As of 2026-07 (editable; --plan-usd overrides).
SUBSCRIPTION = {
    "pro":   (20.0,  "Claude Pro"),
    "max5":  (100.0, "Claude Max (5×)"),
    "max20": (200.0, "Claude Max (20×)"),
    "team":  (30.0,  "Claude Team (per seat)"),
}
TIER_CHOICES = list(SUBSCRIPTION)

CODEXBAR_TIMEOUT = int(os.environ.get("CODEXBAR_TIMEOUT", "60"))
_REDACT = {"accountemail", "email", "account", "identity", "user", "username",
           "name", "token", "accesstoken", "sessionkey", "apikey", "cookie"}


def tier_price(tier: str | None, plan_usd: float | None = None) -> tuple[float | None, str]:
    """(monthly USD, label) for a tier key; plan_usd overrides the table."""
    if plan_usd is not None:
        return plan_usd, (SUBSCRIPTION.get(tier, (0, f"custom ${plan_usd:.0f}/mo"))[1])
    if tier in SUBSCRIPTION:
        usd, label = SUBSCRIPTION[tier]
        return usd, label
    return None, "unknown plan"


def _map_login_to_tier(login: str | None) -> str | None:
    """CodexBar loginMethod / ClaudePlan string -> our tier key. 'Claude Max' can't
    tell 5× from 20×, so it maps to max5 (the common one); override with --plan-usd/--tier."""
    if not login:
        return None
    s = login.lower()
    if "ultra" in s:
        return "max20"
    if "max" in s:
        return "max5"
    if "team" in s:
        return "team"
    if "enterprise" in s:
        return "team"
    if "pro" in s:
        return "pro"
    return None


# ---------------------------------------------------------------- CodexBar discovery + calls

def _candidates() -> list[str]:
    out: list[str] = []
    env = os.environ.get("CODEXBAR_BIN")
    if env:
        out.append(env)
    which = shutil.which("codexbar") or shutil.which("CodexBarCLI")
    if which:
        out.append(which)
    out += [
        "/opt/homebrew/bin/codexbar", "/usr/local/bin/codexbar",
        # dev fallback: a freshly-built fork CLI (works when the installed one is config-broken)
        str(Path.home() / "workplace" / "CodexBar-fork" / ".build" / "release" / "CodexBarCLI"),
        "/Applications/CodexBar.app/Contents/Helpers/CodexBarCLI",
    ]
    seen, uniq = set(), []
    for c in out:
        if c and c not in seen and Path(c).exists():
            seen.add(c)
            uniq.append(c)
    return uniq


def _run(binp: str, *args: str):
    try:
        proc = subprocess.run([binp, *args, "--format", "json", "--json-only"],
                              capture_output=True, text=True, timeout=CODEXBAR_TIMEOUT)
    except Exception:
        return None
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return None
    obj = data[0] if isinstance(data, list) and data else data
    if isinstance(obj, dict) and obj.get("error"):     # config / no-auth error payload
        return None
    return obj


def _healthy(binp: str) -> bool:
    return _run(binp, "cost", "--provider", "claude") is not None


def find_codexbar() -> str | None:
    """First discovered binary that actually answers (skips a config-broken install)."""
    for c in _candidates():
        if _healthy(c):
            return c
    return None


def _redact(obj):
    if isinstance(obj, dict):
        return {k: ("<redacted>" if k.lower() in _REDACT else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


def _window_label(minutes) -> str:
    if not minutes:
        return "window"
    if minutes == 300:
        return "5h session"
    if minutes == 10080:
        return "7-day"
    if minutes % 1440 == 0:
        return f"{minutes // 1440}-day"
    return f"{minutes}min"


def snapshot() -> dict | None:
    """Real tier + current usage/spend from CodexBar, identities redacted. None if
    unavailable (no binary / config-broken / not logged in)."""
    binp = find_codexbar()
    if not binp:
        return None
    out: dict = {"source": "codexbar"}

    usage = _run(binp, "usage", "--provider", "claude")   # needs auth; may be None
    if isinstance(usage, dict):
        u = usage.get("usage", usage)
        login = u.get("loginMethod") or (u.get("identity") or {}).get("loginMethod")
        out["tier"] = _map_login_to_tier(login)
        out["tier_label"] = login
        windows = []
        for key in ("primary", "secondary", "tertiary", "quaternary"):
            w = u.get(key)
            if isinstance(w, dict) and w.get("usedPercent") is not None:
                windows.append({"window": _window_label(w.get("windowMinutes")),
                                "used_percent": w.get("usedPercent"),
                                "resets": w.get("resetDescription") or w.get("resetsAt")})
        for w in (u.get("extraRateWindows") or []):
            if isinstance(w, dict) and w.get("usedPercent") is not None:
                windows.append({"window": w.get("label") or _window_label(w.get("windowMinutes")),
                                "used_percent": w.get("usedPercent"),
                                "resets": w.get("resetDescription") or w.get("resetsAt")})
        out["windows"] = windows

    cost = _run(binp, "cost", "--provider", "claude")     # no auth
    if isinstance(cost, dict):
        out["spend"] = {k: cost.get(k) for k in
                        ("sessionCostUSD", "last30DaysCostUSD", "last30DaysTokens", "sessionTokens")
                        if cost.get(k) is not None}

    if "tier" not in out and "spend" not in out:
        return None
    return _redact(out)


# ---------------------------------------------------------------- reporting

def rank_run_first(ests) -> list:
    """Cheapest-first — quick, cheap signal before the expensive projects."""
    return sorted(ests, key=lambda e: (e.usd_min, e.code_tokens))


def format_block(ests, tier: str | None, plan_usd: float | None, snap: dict | None) -> str:
    """The subscription framing appended under the estimate: real usage (if any),
    run $ + % of plan, and a 'run first' ranking."""
    lines: list[str] = ["", "Subscription"]

    # prefer a real detected tier
    detected = (snap or {}).get("tier")
    eff_tier = tier or detected
    price, label = tier_price(eff_tier, plan_usd)

    if snap:
        if snap.get("tier_label"):
            lines.append(f"  detected plan: {snap['tier_label']}  (via CodexBar)")
        for w in snap.get("windows", []):
            r = f" · resets {w['resets']}" if w.get("resets") else ""
            lines.append(f"    current usage — {w['window']}: {w['used_percent']}% used{r}")
        sp = snap.get("spend") or {}
        if sp.get("last30DaysCostUSD") is not None:
            lines.append(f"    spend to date — 30-day ≈ ${sp['last30DaysCostUSD']:.2f} notional"
                         + (f", session ≈ ${sp['sessionCostUSD']:.2f}" if sp.get("sessionCostUSD") is not None else ""))

    tmin = sum(e.usd_min for e in ests)
    tmax = sum(e.usd_max for e in ests)
    if price:
        lines.append(f"  plan: {label} ≈ ${price:.0f}/mo")
        lines.append(f"  this run: ~${tmin:.2f}–${tmax:.2f} notional  =  "
                     f"{100 * tmin / price:.1f}–{100 * tmax / price:.1f}% of your monthly plan")
        if eff_tier == "max5":
            lines.append(f"    (assuming Max 5×/$100; for Max 20× it's "
                         f"{100 * tmin / 200:.1f}–{100 * tmax / 200:.1f}% of $200/mo — use --plan-usd 200)")
    else:
        lines.append(f"  this run: ~${tmin:.2f}–${tmax:.2f} notional. Pass --tier pro|max5|max20|team "
                     f"(or --plan-usd N) for the % of your plan.")

    lines.append("  run these first (cheapest → dearest):")
    for e in rank_run_first(ests):
        lines.append(f"    ${e.usd_min:>5.2f}  {Path(e.project).name}")
    return "\n".join(lines)


if __name__ == "__main__":
    # quick manual check: print the redacted snapshot
    snap = snapshot()
    print(json.dumps(snap, indent=2, default=str) if snap else "CodexBar unavailable (no binary / config-broken / not logged in)")
