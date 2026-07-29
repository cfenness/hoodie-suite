#!/usr/bin/env python3
"""adapt.py — one switch that holds every adaptive controller still.

WHY THIS EXISTS. Three controllers now change our behaviour in response to what the target does: `pace`
moves the request rate, `sessions` moves the identity budget, `ladder` changes TLS costume and rung. That
is correct in production and fatal during a measurement — an experiment that varies concurrency while the
ladder quietly swaps costume at 50% blocked is not measuring concurrency, it is measuring the ladder. The
arm would come back "clean" for the wrong reason and the conclusion would be confidently wrong.

This is the same failure the handoff names as the expensive one: a system reporting a good number that
nothing earned. A controller adapting mid-experiment produces exactly that, and it produces it silently,
because every individual component is behaving correctly.

So: freeze the loop, take the reading, unfreeze.

    ADAPT_FROZEN=1 python3 costume_probe.py ...       # from the outside
    adapt.freeze(); ...; adapt.unfreeze()             # from a harness or a test

FROZEN MEANS OBSERVE, NOT ACT. Everything still classifies, counts and records — `blocks` keeps its full
tally, `pace` keeps its statistics — because the measurement IS those numbers. What stops is the feedback:
no rate change, no budget change, no costume rotation, no rung escalation.

Deliberately NOT the default and deliberately loud to leave on: a frozen ladder in production is a system
that cannot escalate away from a closed path, which is the exact reflex `ladder.py` was written to add.
Anything long-running should assert `not adapt.frozen()` rather than trust the environment.
"""
import os

_FORCED = [None]                   # None = defer to the environment; True/False = explicit override


def frozen():
    """True when adaptive feedback is suspended. Env `ADAPT_FROZEN` unless overridden in-process."""
    if _FORCED[0] is not None:
        return _FORCED[0]
    return os.environ.get("ADAPT_FROZEN", "").strip().lower() in ("1", "true", "yes", "on")


def freeze():
    _FORCED[0] = True


def unfreeze():
    _FORCED[0] = False


def clear():
    """Drop the in-process override and go back to reading the environment."""
    _FORCED[0] = None
