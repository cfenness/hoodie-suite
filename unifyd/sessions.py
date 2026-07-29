#!/usr/bin/env python3
"""sessions.py — a session is INVENTORY THAT DEPRECIATES. Manage its lifecycle, don't guess its size.

WHAT THIS IS FOR. The binding constraint on our hardest source turned out not to be IPs, concurrency or
request rate — it was how many requests one primed cookie is allowed before the target stops answering.
Measured across three runs, collapse tracked request COUNT, not elapsed time:

    unpaced, 120 workers    healthy ~60s, ~5,578 requests   (~46 per session)
    paced at 40 req/s       healthy ~60s, ~7,000 requests   (~58 per session)

Halving the rate bought only a longer wait for the same wall. Re-priming every 40 requests took success
from 5.7% to 88-93%. Twenty exit IPs never helped, because the quota was never per-IP.

WHY IT LEARNS RATHER THAN HOLDS A CONSTANT. `SESSION_BUDGET = 40` is the fifth constant this system has
depended on, and the previous four were all wrong within a day — each measured under conditions that had
already changed by the time it was used. A budget that is right for UberEats today is not right for
UberEats next month, and is certainly not right for a different source. So the budget is a STARTING
POINT that the burn evidence corrects.

HOW A BURN IS DETECTED. This is what the block classifier bought us: a session that starts returning
`soft_block` / `captcha` / `rate_limited` has burned, and we know its exact age when it did. A session
that hits `not_found` has not burned — that is the target's catalogue, not our standing. Without that
distinction (which we lacked until today) every closed store would look like a spent identity and the
budget would ratchet toward zero, which is precisely how the pacer throttled itself to a standstill.

    pol = sessions.policy_for("ubereats")     # registry-declared, per source
    ...
    if pol.spent(n_requests): reprime()
    pol.report(cls, age)                      # feeds the learned burn point
"""
import os
import threading

DEFAULT_BUDGET = int(os.environ.get("SESSION_BUDGET_DEFAULT", "40"))
MIN_BUDGET = int(os.environ.get("SESSION_BUDGET_MIN", "5"))
MAX_BUDGET = int(os.environ.get("SESSION_BUDGET_MAX", "500"))
# Keep the budget under the observed burn — re-priming costs one cheap request, burning costs the
# session AND poisons every response until we notice. Asymmetric cost, asymmetric margin.
SAFETY = float(os.environ.get("SESSION_BUDGET_SAFETY", "0.7"))


class SessionPolicy:
    """One source's session lifecycle: how long an identity lasts, and what we learn when one burns."""

    __slots__ = ("source", "budget", "_start", "_burns", "_observed", "_lock", "_retired", "_primed")

    def __init__(self, source, budget=None):
        self.source = source
        self.budget = int(budget or DEFAULT_BUDGET)
        self._start = self.budget
        self._burns = 0
        self._observed = None          # EWMA of the age at which sessions actually burn
        self._retired = 0
        self._primed = 0
        self._lock = threading.Lock()

    def spent(self, age):
        """Should a session this old be replaced? `age` is requests served by that session."""
        return bool(self.budget) and age >= self.budget

    def report(self, cls, age):
        """Feed one outcome. Only genuine pushback counts as a burn — a closed store says nothing about
        the health of our identity, and treating it as evidence is how a controller talks itself into
        the floor."""
        try:
            import adapt
            if adapt.frozen():
                return False              # measuring: the budget must not move under the experiment
        except Exception:
            pass
        try:
            import blocks
            burned = blocks.is_throttle(cls)
        except Exception:
            burned = False
        if not burned or age <= 0:
            return False
        with self._lock:
            self._burns += 1
            # EWMA so one unlucky session cannot swing the budget, but a real shift moves it quickly.
            self._observed = float(age) if self._observed is None else (0.7 * self._observed + 0.3 * age)
            target = max(MIN_BUDGET, min(MAX_BUDGET, int(self._observed * SAFETY)))
            self.budget = target
        return True

    def note_prime(self):
        with self._lock:
            self._primed += 1

    def note_retire(self):
        with self._lock:
            self._retired += 1

    def stats(self):
        with self._lock:
            return {"source": self.source, "budget": self.budget, "started_at": self._start,
                    "burns": self._burns, "primed": self._primed, "retired": self._retired,
                    "observed_burn": (round(self._observed, 1) if self._observed is not None else None)}


_POLICIES = {}
_LOCK = threading.Lock()


def policy_for(source, budget=None):
    """The policy for one source. Budget precedence: explicit arg > registry `session_budget` > default.
    Declaring it in the registry is the point — session lifecycle is a per-DOMAIN property, the same as
    its parser and its rate policy, and it belongs in the playbook rather than hard-coded in a fetcher."""
    with _LOCK:
        if source in _POLICIES:
            return _POLICIES[source]
    if budget is None:
        try:
            import source_registry
            for s in getattr(source_registry, "SOURCES", []):
                if s.get("id") == source:
                    budget = s.get("session_budget")
                    break
        except Exception:
            budget = None
    p = SessionPolicy(source, budget)
    with _LOCK:
        return _POLICIES.setdefault(source, p)


def reset():
    with _LOCK:
        _POLICIES.clear()
