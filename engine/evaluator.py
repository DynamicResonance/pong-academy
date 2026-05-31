"""
FROZEN EVALUATOR  —  Antler pre-seed wedge scorer.

This file is the immutable evaluation contract for the Pong Academy Autoresearch
loop. It implements the rubric in inputs/Antler.md EXACTLY:

  * 10 weighted criteria summing to 100 (weights below, copied verbatim).
  * Two scores per wedge: TODAY (evidence that exists right now) and PROJECTED
    (after the single 4-week experiment succeeds).
  * 5 mandatory hard gates -> pass / at_risk / fail.
  * Evidence mapped to the quantified weak / minimum / strong thresholds table.

GUARDRAIL: The agent / proposer LLM may NEVER edit this file or the weights to
inflate a score. run_loop.py records the SHA-256 of this file into loops.json so
the run is auditable. The proposer only fills the wedge schema (the "policy");
the mapping from evidence -> score lives here and is fixed.

HARSHNESS: TODAY traction is enforced to reality by the harness (see
freeze_today_evidence). We have NO customer tests yet, so all discovery counts,
design partners, LOIs, paid customers and waitlist are floored at zero for the
TODAY state regardless of what the proposer claims. The only TODAY assets are
founder-market fit (real resume facts), third-party demand pull (the VC RFPs),
a demonstrable core loop on the physical rig, and reasoning-grade market/why-now
arguments. "Strong pass (2)" on distribution / market-expansion / moat criteria
is CAPPED for the TODAY state because those columns explicitly require proof we
do not have yet; the caps lift only as the experiment produces real evidence.
"""

import hashlib

# ---------------------------------------------------------------------------
# Weights — copied verbatim from Antler.md (sum = 100). NEVER change to win.
# ---------------------------------------------------------------------------
WEIGHTS = {
    "c1_painful_problem":      15,
    "c2_specific_wedge":       12,
    "c3_founder_market_fit":   15,
    "c4_evidence_of_demand":   18,
    "c5_mvp_feasibility":      10,
    "c6_market_expansion":     10,
    "c7_differentiation":       8,
    "c8_business_model":        6,
    "c9_timing_whynow":         4,
    "c10_story_clarity":        2,
}
assert sum(WEIGHTS.values()) == 100

CRITERIA_LABELS = {
    "c1_painful_problem":    "Painful problem in narrow beachhead",
    "c2_specific_wedge":     "Specific wedge, not generic TAM",
    "c3_founder_market_fit": "Founder-market fit",
    "c4_evidence_of_demand": "Evidence of demand",
    "c5_mvp_feasibility":    "Product proof / MVP feasibility",
    "c6_market_expansion":   "Market size + expansion path",
    "c7_differentiation":    "Differentiation / defensibility",
    "c8_business_model":     "Business model + unit economics",
    "c9_timing_whynow":      "Timing / market trend",
    "c10_story_clarity":     "Fundraise readiness / story clarity",
}


# ---------------------------------------------------------------------------
# Threshold helpers  (weak / minimum / strong -> 0..1 fraction of criterion)
# ---------------------------------------------------------------------------
def lvl3(x):
    """Enum 0/1/2 -> weak / minimum / strong fraction."""
    return {0: 0.30, 1: 0.65, 2: 1.00}.get(int(x), 0.30)


def interviews_score(n):
    # Antler thresholds: <10 weak, 20-30 minimum, 50+ strong.
    n = int(n)
    if n <= 0:   return 0.00
    if n < 10:   return 0.30
    if n < 20:   return 0.50
    if n < 50:   return 0.70
    return 1.00


def design_partners_score(n):
    # 0 fail, 2-3 minimum, 5+ strong.
    n = int(n)
    if n <= 0:   return 0.00
    if n == 1:   return 0.40
    if n < 5:    return 0.70
    return 1.00


def loi_score(n):
    n = int(n)
    if n <= 0:   return 0.00
    if n < 3:    return 0.65
    return 1.00


def paid_score(n):
    n = int(n)
    if n <= 0:   return 0.00
    if n < 3:    return 0.65
    return 1.00


def waitlist_score(n):
    n = int(n)
    if n < 100:  return 0.00
    if n < 2000: return 0.55
    return 1.00


# ---------------------------------------------------------------------------
# TODAY reality floor — enforced by the harness, NOT editable by the proposer.
# We have no customer tests yet; only founder pedigree + RFP pull + a core loop.
# ---------------------------------------------------------------------------
def freeze_today_evidence(qual):
    """Return the demand/traction evidence dict for the TODAY state.

    All real-world traction is floored at zero. A concierge-grade demonstration
    of the autoresearch loop on the physical pong rig is allowed (prototype=1)
    because that artifact genuinely exists, but it is NOT the wedge product, so
    it caps there. Willingness-to-pay is hypothetical today (nobody has paid).
    sales_cycle may reflect reasoning (buyer process mapped) but is capped at 1.
    """
    return {
        "discovery_interviews": 0,
        "design_partners":      0,
        "lois":                 0,
        "paid_customers":       0,
        "waitlist":             0,
        "prototype":            1,   # core loop demonstrable on the rig (concierge)
        "retention_signal":     0,
        "willingness_to_pay":   0,   # hypothetical
        "sales_cycle":          min(int(qual.get("sales_cycle", 0)), 1),
    }


# ---------------------------------------------------------------------------
# The scorer.  state = "today" | "projected".
# ---------------------------------------------------------------------------
def _criterion_scores(qual, dem, state):
    """Return {criterion: fraction 0..1}. Pure function of evidence."""
    is_today = (state == "today")

    # C1 — Painful problem in narrow beachhead. Interviews dominate ("strong
    # pass" requires customer interviews showing pain); RFP pull + buyer-spend
    # provide third-party support.
    c1 = (0.35 * lvl3(qual["problem_intensity"])
          + 0.45 * interviews_score(dem["discovery_interviews"])
          + 0.10 * (1.0 if qual.get("rfp_match") else 0.0)
          + 0.10 * (1.0 if qual.get("buyer_already_spends") else 0.0))

    # C2 — Specific wedge (ICP sharpness + why-now). Reasoning artifact, but the
    # "strong pass" distribution-path proof is unproven until a design partner
    # exists, so TODAY this is capped at minimum-pass.
    c2 = 0.70 * lvl3(qual["icp_specificity"]) + 0.30 * lvl3(qual["why_now"])
    if is_today or dem["design_partners"] < 1:
        c2 = min(c2, 0.65)

    # C3 — Founder-market fit. Inherent / real (resume facts). Never capped.
    c3 = lvl3(qual["founder_fit"])

    # C4 — Evidence of demand. The validation criterion; near floor today.
    c4 = (0.30 * interviews_score(dem["discovery_interviews"])
          + 0.30 * design_partners_score(dem["design_partners"])
          + 0.20 * loi_score(dem["lois"])
          + 0.20 * paid_score(dem["paid_customers"]))
    c4 = max(c4, waitlist_score(dem["waitlist"]))          # waitlist alt path
    c4 += 0.15 * (1.0 if qual.get("rfp_match") else 0.0)   # third-party pull
    c4 = min(c4, 1.0)

    # C5 — MVP feasibility (prototype + retention signal).
    c5 = 0.70 * lvl3(dem["prototype"]) + 0.30 * lvl3(dem["retention_signal"])

    # C6 — Market size + expansion path. $1B+ adjacency cannot be proven in one
    # 4-week experiment, so the strong column is capped unless real expansion
    # evidence (>=2 design partners) exists; never reaches 1.0 from one sprint.
    c6 = lvl3(qual["market_beachhead"])
    if is_today or dem["design_partners"] < 2:
        c6 = min(c6, 0.65)
    else:
        c6 = min(c6, 0.80)

    # C7 — Differentiation / defensibility. The autoresearch data flywheel is a
    # real architectural moat, but it is unproven without usage; capped until a
    # prototype + design partner exist, full only with retention.
    c7 = lvl3(qual["differentiation"])
    if is_today:
        c7 = min(c7, 0.70)
    elif dem["retention_signal"] < 2:
        c7 = min(c7, 0.85)

    # C8 — Business model + unit economics (payer clarity + willingness to pay).
    c8 = 0.60 * lvl3(qual["business_model"]) + 0.40 * lvl3(dem["willingness_to_pay"])

    # C9 — Timing / why-now. Reasoning + external tailwind (RFPs).
    c9 = lvl3(qual["why_now"])

    # C10 — Story clarity.
    c10 = lvl3(qual["story_clarity"])

    return {
        "c1_painful_problem":    c1,
        "c2_specific_wedge":     c2,
        "c3_founder_market_fit": c3,
        "c4_evidence_of_demand": c4,
        "c5_mvp_feasibility":    c5,
        "c6_market_expansion":   c6,
        "c7_differentiation":    c7,
        "c8_business_model":     c8,
        "c9_timing_whynow":      c9,
        "c10_story_clarity":     c10,
    }


def _hard_gates(qual, dem, state):
    """5 mandatory Antler gates -> 'pass' | 'at_risk' | 'fail'."""
    gates = {}

    # G1 Founder-market fit: clear unfair advantage.
    gates["G1_founder_market_fit"] = "pass" if qual["founder_fit"] >= 1 else "fail"

    # G2 Problem-solution fit: deep problem tested with early evangelists.
    if dem["discovery_interviews"] >= 10 or dem["design_partners"] >= 1:
        gates["G2_problem_solution_fit"] = "pass"
    elif qual.get("rfp_match") and qual["icp_specificity"] >= 2:
        gates["G2_problem_solution_fit"] = "at_risk"   # third-party proxy only
    else:
        gates["G2_problem_solution_fit"] = "fail"

    # G3 Validation beyond vision: >=1 of paid / pilot / LOI / design partner /
    # fast waitlist / strong qual+quant. THE pre-seed gate.
    if (dem["paid_customers"] >= 1 or dem["lois"] >= 1 or dem["design_partners"] >= 1
            or dem["waitlist"] >= 500
            or (dem["discovery_interviews"] >= 20 and dem["willingness_to_pay"] >= 1)):
        gates["G3_validation_beyond_vision"] = "pass"
    else:
        gates["G3_validation_beyond_vision"] = "fail"   # nothing yet -> fails today

    # G4 Scalable venture outcome: TAM + timing + scalable tech.
    if qual["market_beachhead"] >= 1 and qual["why_now"] >= 1:
        gates["G4_scalable_outcome"] = "pass"
    else:
        gates["G4_scalable_outcome"] = "at_risk"

    # G5 Compelling story: strong team + clear opportunity.
    if qual["story_clarity"] >= 1 and qual["founder_fit"] >= 1:
        gates["G5_compelling_story"] = "pass"
    else:
        gates["G5_compelling_story"] = "at_risk"

    return gates


def score_state(qual, dem, state):
    fr = _criterion_scores(qual, dem, state)
    breakdown = {}
    total = 0.0
    for k, w in WEIGHTS.items():
        pts = round(fr[k] * w, 2)
        breakdown[k] = {"label": CRITERIA_LABELS[k], "weight": w,
                        "fraction": round(fr[k], 3), "points": pts}
        total += pts
    gates = _hard_gates(qual, dem, state)
    return {"total": round(total, 1), "breakdown": breakdown, "gates": gates}


def evaluate(wedge):
    """Score a filled wedge schema. Returns today + projected results.

    wedge["evidence"]["qual"]                      -> reasoning-grade enums (proposer)
    wedge["evidence"]["projected_experiment_targets"] -> demand state IF experiment succeeds
    TODAY demand state is frozen by the harness (no invented traction).
    """
    ev = wedge["evidence"]
    qual = ev["qual"]
    today_dem = freeze_today_evidence(qual)
    proj_dem = ev["projected_experiment_targets"]

    today = score_state(qual, today_dem, "today")
    projected = score_state(qual, proj_dem, "projected")

    # gates "at risk" for surfacing = anything not a clean pass in the TODAY state
    at_risk = [g for g, v in today["gates"].items() if v != "pass"]

    return {
        "today_score": today["total"],
        "projected_score": projected["total"],
        "today": today,
        "projected": projected,
        "today_evidence_used": today_dem,
        "gates_at_risk_today": at_risk,
        "band_today": band(today["total"]),
        "band_projected": band(projected["total"]),
    }


def band(score):
    if score >= 85: return "Strong Antler-fundable wedge"
    if score >= 70: return "Potentially fundable; needs sharper proof or story"
    if score >= 55: return "Interesting but under-validated"
    return "Not fundable yet"


def frozen_hash():
    with open(__file__, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


if __name__ == "__main__":
    # Self-test / calibration with a strong well-fit wedge.
    demo = {
        "evidence": {
            "qual": {
                "problem_intensity": 2, "icp_specificity": 2, "founder_fit": 2,
                "why_now": 2, "rfp_match": True, "differentiation": 2,
                "market_beachhead": 2, "business_model": 2, "buyer_already_spends": True,
                "story_clarity": 2, "sales_cycle": 1,
            },
            "projected_experiment_targets": {
                "discovery_interviews": 22, "design_partners": 2, "lois": 1,
                "paid_customers": 0, "waitlist": 0, "prototype": 1,
                "retention_signal": 1, "willingness_to_pay": 1, "sales_cycle": 1,
            },
        }
    }
    r = evaluate(demo)
    print("frozen_hash:", frozen_hash())
    print("today:", r["today_score"], "|", r["band_today"])
    print("projected:", r["projected_score"], "|", r["band_projected"])
    print("gates at risk today:", r["gates_at_risk_today"])
