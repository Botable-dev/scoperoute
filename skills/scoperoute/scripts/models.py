#!/usr/bin/env python3
"""models.py — one declarative source of truth for model IDs, roles and pricing.

Before this, the model configuration was scattered: FABLE_MODEL / CONTROL_MODELS /
ADJUDICATOR_MODEL in scoperoute.py, hard-coded "claude-sonnet-5" / "claude-opus-4-8"
inside recon()/summarize_arch(), and a separate PRICING table in estimate.py. A model
id or price change meant editing several files and risking drift between what the
engine runs and what the estimator prices. This is the single table; everything
imports from here.

Stdlib only.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Model:
    id: str
    price_in: float       # notional list price, USD / 1M input tokens
    price_out: float      # notional list price, USD / 1M output tokens
    note: str = ""


# Notional list prices as of 2026-06-24 (CLI/subscription spends quota, not $; these
# let the estimator compare runs and size them against a plan).
FABLE = Model("claude-fable-5", 10.0, 50.0)
OPUS = Model("claude-opus-4-8", 5.0, 25.0)
SONNET = Model("claude-sonnet-5", 2.0, 10.0, "intro pricing through 2026-08-31 (else 3.0/15.0)")
HAIKU = Model("claude-haiku-4-5", 1.0, 5.0)

# Roles in the pipeline (referenced by both the engine and the estimator).
PROBE_MODEL = FABLE            # the model whose cooperation we measure
RECON_MODEL = SONNET          # agentic recon reads the repo itself (low effort)
SUMMARY_MODEL = OPUS          # arch summary + real-code curation
ADJUDICATOR = OPUS            # structured tie-break on *_ambiguous

# Controls run on the tripped variant only: (model, effort). Opus at high, Sonnet at
# low — cheap, and per the cost note Sonnet is never worth more than high.
CONTROLS = ((OPUS, "high"), (SONNET, "low"))

# id -> (price_in, price_out), the shape estimate.py's _cost() expects.
PRICING = {m.id: (m.price_in, m.price_out) for m in (FABLE, OPUS, SONNET, HAIKU)}


def control_pairs() -> list[tuple[str, str]]:
    """(model_id, effort) list — the legacy CONTROL_MODELS shape used across the engine."""
    return [(m.id, effort) for m, effort in CONTROLS]
