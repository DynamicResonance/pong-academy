"""
RUN LOOP — the real Autoresearch loop adapted to wedge-finding.

Contract (mirrors autoresearch-master/program.md):
  propose (live LLM, mutable policy)  ->  score (FROZEN evaluator)  ->  keep/rollback.

  * Frozen evaluator   = engine/evaluator.py (never edited by the agent; its
                         SHA-256 is recorded into loops.json for audit).
  * Mutable policy     = the wedge spec the LLM fills.
  * Keep/rollback      = keep the proposal if its PROJECTED Antler score beats the
                         current champion; else roll back (discard) and try a
                         different hypothesis next loop.

Every loop is logged to outputs/loops.json. All scored wedges are ranked by
projected score; the top 3 are written to outputs/top3.json for the HTML build.
"""

import json
import os
import sys
import time
import datetime
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import evaluator
import propose as proposer

N_LOOPS = 10

# Distinct, grounded directives. First six explore independent hypotheses
# (fresh seeds from founder edge + RFP signals); the last four mutate/recombine
# the current champion. Candidates are fully open — these only steer the search.
DIRECTIVES = [
    ("seed", "BASELINE seed. The most direct B2B wedge: ship Pong Academy as a self-"
             "calibration module for industrial robot arms that auto-tunes to one factory "
             "station (lighting/vibration/fixture drift) in minutes. Lean on a16z 'Renaissance "
             "of the American factory' + 'AI-native industrial base', and Ed's perception + "
             "Aleh's enterprise B2B (AB InBev) edge."),
    ("seed", "Fresh hypothesis: precision-ag actuation calibration — weed-zapping / fruit-pick "
             "robots that self-tune actuation timing + camera angles per orchard. Match YC S26 "
             "#1 Low-Pesticide Agriculture (Garry Tan; strawberries $3B CA, 60% labor)."),
    ("seed", "Fresh hypothesis: autonomous-lab manipulation calibration — close the loop on "
             "robotic lab instruments so lights-out labs self-tune per protocol. Match a16z "
             "'Autonomous labs' (directly relevant to an autoresearch product)."),
    ("seed", "Fresh hypothesis: counter-drone / interceptor aim calibration — a constrained "
             "measurable intercept loop that self-tunes per site/sensor rig. Match YC S26 #5 "
             "Counter-Swarm Defense. Note Ed's Saronic (defense) perception background."),
    ("seed", "Fresh hypothesis: the 'data crusade' coordination stack — sell the episode-capture "
             "+ RL-environment + auto-calibration loop itself as infrastructure to robotics teams "
             "drowning in sim-to-real tuning. Match a16z 'Data crusade in critical industries'."),
    ("seed", "Fresh hypothesis: Shenzhen-speed robotic-cell bring-up — collapse weeks of integrator "
             "calibration on a new line to hours. Match YC S26 #8 Hardware Supply Chain + a16z factory."),
    ("mutate", "Mutate the champion: sharpen the ICP to a SINGLE role + trigger + budget owner and "
               "the narrowest possible beachhead. Make problem_intensity defensible (buyer already "
               "spends money/time on this exact task today)."),
    ("mutate", "Mutate the champion: strengthen defensibility to a true data/distribution moat — the "
               "proprietary per-environment episode flywheel + an integrator/OEM distribution wedge. "
               "Re-confirm the single best-matched RFP."),
    ("mutate", "Mutate the champion: make the 4-week experiment decisive with a hard quantified "
               "go/no-go on a real partner rig, and tighten the business model (payer, price, margin, "
               "CAC channel, expansion revenue)."),
    ("mutate", "Recombine: take the champion's sharpest ICP and graft the strongest adjacency map for "
               "a $1B+ expansion path, framed on the rare GTM+perception founder pair. Maximize the "
               "honest projected score without inventing traction."),
]


def load_env():
    env = ROOT / ".env.local"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if v:
                os.environ.setdefault(k.strip(), v)


def sanitize(wedge):
    """Coerce enum fields to ints; ensure required structure exists."""
    q = wedge["evidence"]["qual"]
    for k in ["problem_intensity", "icp_specificity", "founder_fit", "why_now",
              "differentiation", "market_beachhead", "business_model",
              "story_clarity", "sales_cycle"]:
        q[k] = max(0, min(2, int(q.get(k, 0))))
    for b in ["rfp_match", "buyer_already_spends"]:
        q[b] = bool(q.get(b, False))
    p = wedge["evidence"]["projected_experiment_targets"]
    for k in ["discovery_interviews", "design_partners", "lois", "paid_customers", "waitlist"]:
        p[k] = max(0, int(p.get(k, 0)))
    for k in ["prototype", "retention_signal", "willingness_to_pay", "sales_cycle"]:
        p[k] = max(0, min(2, int(p.get(k, 0))))
    return wedge


def propose_with_retry(facts, history, parent, directive, loop_idx, tries=3):
    last = None
    for t in range(tries):
        try:
            wedge, usage = proposer.propose(facts, history, parent, directive, loop_idx)
            return sanitize(wedge), usage
        except Exception as e:  # noqa
            last = e
            print(f"    ! propose attempt {t+1} failed: {e}", flush=True)
            time.sleep(2 + 2 * t)
    raise RuntimeError(f"propose failed after {tries} tries: {last}")


def main():
    load_env()
    facts = json.loads((HERE / "facts.json").read_text())
    fhash = evaluator.frozen_hash()
    print(f"Frozen evaluator SHA-256: {fhash}", flush=True)
    print(f"Proposer model: {proposer.MODEL}\n", flush=True)

    loops = []
    history = []          # compact view passed to the proposer
    wedges = []           # full scored wedges (for ranking)
    champion = None       # the current best wedge dict (the 'branch head')
    champion_proj = -1.0

    for i in range(N_LOOPS):
        kind, directive = DIRECTIVES[i]
        wid = f"w{i+1:02d}"
        parent = champion if kind == "mutate" else None
        parent_id = champion["id"] if (parent is not None) else None
        parent_proj = champion_proj if (parent is not None) else None
        print(f"[loop {i+1}/{N_LOOPS}] {kind:6s} parent={parent_id or '-'} ...", flush=True)

        wedge, usage = propose_with_retry(facts, history, parent, directive, i + 1)
        wedge["id"] = wid
        wedge["kind"] = kind
        wedge["parent"] = parent_id
        wedge["loop"] = i + 1

        res = evaluator.evaluate(wedge)
        wedge["scores"] = res

        # keep/rollback: did PROJECTED improve over the champion we'd advance from?
        if champion is None:
            status = "baseline-keep"
            keep = True
        elif res["projected_score"] > champion_proj:
            status = "keep"
            keep = True
        else:
            status = "discard-rollback"
            keep = False
        if keep:
            champion = wedge
            champion_proj = res["projected_score"]

        loops.append({
            "id": wid, "loop": i + 1, "kind": kind, "parent": parent_id,
            "name": wedge["name"], "mutation": wedge.get("mutation", ""),
            "today_score": res["today_score"], "projected_score": res["projected_score"],
            "delta_vs_parent": (None if parent_proj is None
                                else round(res["projected_score"] - parent_proj, 1)),
            "status": status, "band_today": res["band_today"],
            "band_projected": res["band_projected"],
            "gates_at_risk_today": res["gates_at_risk_today"],
            "rfp_match": wedge["evidence"]["qual"].get("rfp_which", ""),
            "tokens": usage,
        })
        history.append({"id": wid, "name": wedge["name"],
                        "today_score": res["today_score"],
                        "projected_score": res["projected_score"], "status": status})
        wedges.append(wedge)
        print(f"    -> {wedge['name']!r}  today={res['today_score']}  "
              f"projected={res['projected_score']}  [{status}]", flush=True)

    # rank ALL candidates by projected score (mission: top 3 by projected), but
    # take the best wedge per FAMILY so the Top 3 are 3 genuinely distinct wedges
    # rather than near-duplicate mutations of the same champion.
    ranked = sorted(wedges, key=lambda w: w["scores"]["projected_score"], reverse=True)
    top3, seen_families = [], set()
    for w in ranked:
        fam = w.get("family") or w["id"]
        if fam in seen_families:
            continue
        seen_families.add(fam)
        top3.append(w)
        if len(top3) == 3:
            break

    any_today_70 = any(w["scores"]["today_score"] >= 70 for w in wedges)
    best_today = max(wedges, key=lambda w: w["scores"]["today_score"])

    out = {
        "meta": {
            "generated": datetime.datetime.now().isoformat(timespec="seconds"),
            "frozen_evaluator_sha256": fhash,
            "proposer_model": proposer.MODEL,
            "n_loops": N_LOOPS,
            "weights": evaluator.WEIGHTS,
            "criteria_labels": evaluator.CRITERIA_LABELS,
            "contract": "Karpathy autoresearch: frozen evaluator + mutable policy (wedge) + propose->score->keep/rollback",
        },
        "loops": loops,
        "wedges": wedges,
        "top3_ids": [w["id"] for w in top3],
        "verdict": {
            "any_candidate_clears_70_today": any_today_70,
            "best_today_id": best_today["id"],
            "best_today_name": best_today["name"],
            "best_today_score": best_today["scores"]["today_score"],
        },
    }
    (ROOT / "outputs" / "loops.json").write_text(json.dumps(out, indent=2))
    print(f"\nWrote outputs/loops.json  ({len(loops)} loops)")
    print("Top 3 by projected score:")
    for r, w in enumerate(top3, 1):
        print(f"  {r}. {w['name']}  today={w['scores']['today_score']}  "
              f"projected={w['scores']['projected_score']}")
    print(f"Any candidate >=70 TODAY? {'YES' if any_today_70 else 'NO'} "
          f"(best today = {best_today['scores']['today_score']}: {best_today['name']})")


if __name__ == "__main__":
    main()
