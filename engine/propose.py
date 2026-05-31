"""
PROPOSE STEP — live LLM call (Anthropic API, model claude-sonnet-4-20250514).

The LLM is the MUTABLE POLICY: it proposes a new wedge, or mutates a parent
wedge, grounded ONLY in the extracted source facts (facts.json). It fills the
Antler wedge schema. It NEVER sees or touches the frozen evaluator, and it does
NOT get to pick its own scores — it sets reasoning-grade evidence enums (which
it must justify with a doc citation) plus the projected 4-week experiment
targets. The harness floors all TODAY traction to reality.

Key is read from env ANTHROPIC_API_KEY (loaded from .env.local by run_loop.py).
Never hardcode the key.
"""

import json
import os
import anthropic

MODEL = "claude-sonnet-4-20250514"

SCHEMA_SPEC = """
Return ONLY a single JSON object (no prose, no markdown fence) with EXACTLY these keys:

{
  "name": "<= 6 word wedge name",
  "family": "one stable tag for the wedge family, e.g. 'industrial-arms' | 'precision-ag' | 'autonomous-labs' | 'counter-drone' | 'robotics-infra' | 'robot-cell-commissioning' (reuse the SAME tag when a mutation stays in the same family)",
  "tagline": "one punchy sentence",
  "mutation": "if this mutates a parent: what you changed and why; else 'seed: <origin>'",
  "icp": "the exact beachhead: role + workflow + trigger + budget owner",
  "problem": "the painful, repeated, budgeted problem in that beachhead",
  "why_now": "the platform/regulation/budget/behavior shift making this possible NOW",
  "demand_evidence": "the third-party pull. MUST name the specific RFP id+title it matches and why",
  "mvp_plan": "the concierge/working MVP that proves the outcome",
  "market_expansion": "$100M+ beachhead and the $1B+ adjacency map",
  "defensibility": "the data/workflow/distribution moat (the autoresearch episode flywheel) - not 'we use AI'",
  "business_model": "payer, pricing, gross-margin logic, CAC channel, expansion revenue",
  "experiment_4wk": "the SINGLE 4-week experiment that would validate the riskiest assumption",
  "go_no_go": "one explicit quantified go/no-go number (e.g. '>=2 signed paid pilots' or '>=30% relative success-rate lift on a partner rig in <=300 episodes')",
  "why_pitch_antler": "2 sentences: why this is the one to pitch Antler",
  "evidence": {
    "qual": {
      "problem_intensity": 0,
      "icp_specificity": 0,
      "founder_fit": 0,
      "founder_fit_justification": "cite SPECIFIC resume facts for Aleh and/or Ed",
      "why_now": 0,
      "rfp_match": true,
      "rfp_which": "RFP id + title",
      "differentiation": 0,
      "market_beachhead": 0,
      "business_model": 0,
      "problem_intensity_justification": "cite the doc evidence",
      "buyer_already_spends": true,
      "story_clarity": 0,
      "sales_cycle": 0
    },
    "projected_experiment_targets": {
      "discovery_interviews": 0,
      "design_partners": 0,
      "lois": 0,
      "paid_customers": 0,
      "waitlist": 0,
      "prototype": 0,
      "retention_signal": 0,
      "willingness_to_pay": 0,
      "sales_cycle": 0
    }
  }
}

ENUM CODES (use 0/1/2 = weak/minimum/strong per the Antler thresholds):
- problem_intensity: 0 nice-to-have | 1 repeated weekly/monthly pain | 2 urgent AND buyer already budgets for it
- icp_specificity: 0 generic persona | 1 one role/use-case | 2 role + trigger + budget owner
- founder_fit: 0 no clear reason | 1 founder can credibly explain why suited | 2 direct lived/domain expertise + network + prior company-building proof
- why_now: 0 none | 1 some tailwind | 2 strong regulation/platform/budget/behavior shift
- differentiation: 0 AI wrapper | 1 workflow-specific advantage | 2 data/protocol/distribution moat
- market_beachhead: 0 vague big-market claim | 1 plausible $100M+ beachhead | 2 $1B+ expansion path with adjacency map
- business_model: 0 payer unclear | 1 plausible pricing + buyer identified | 2 clear payer + pricing + margin + CAC channel + expansion
- story_clarity: 0 cannot explain in 30s | 1 basic deck/memo | 2 crisp one-pager with metrics + ask + milestone
- sales_cycle: 0 unknown | 1 buyer process mapped | 2 known buyer + budget + procurement path
- prototype: 0 none | 1 clickable/concierge demo | 2 working MVP with usage
- retention_signal: 0 none | 1 repeat usage by early testers | 2 30%+ weekly active return
- willingness_to_pay: 0 hypothetical | 1 buyer names price range | 2 signed pilot / paid trial

projected_experiment_targets = the realistic evidence state IF the single 4-week experiment SUCCEEDS.
Be realistic: a 4-week pre-seed sprint typically yields 15-30 discovery interviews, 1-3 design
partners, 0-1 LOI, a concierge-grade prototype with early repeat usage, and a buyer naming a price.
Do NOT claim paid customers or 50+ interviews from one 4-week sprint unless genuinely justified.
"""

SYSTEM = """You are the proposer in a frozen-evaluator Autoresearch loop (Karpathy's
autoresearch contract) finding the best Antler pre-seed WEDGE for 'Pong Academy', an
agentic self-improving robotics calibration loop.

HARD RULES:
- Ground EVERY claim only in the provided source facts. Cite specific RFP ids and
  specific resume facts. Never invent customers, traction, interviews, or pilots.
- The wedge must exploit Pong Academy's actual mechanic: a constrained, MEASURABLE action
  loop in a HIGHLY VARIABLE real environment where the sim-to-real gap is today closed by
  expensive human engineering hours. That is the pattern to match.
- Output ONLY the JSON object. No commentary.

CALIBRATION — BE HONEST AND HARSH. Do NOT default every enum to 2. Reserve 2 for
genuinely strong evidence; most pre-seed wedges deserve a mix of 0s, 1s and 2s.

- founder_fit: this is the team's real edge but it is WEDGE-SPECIFIC.
    * Score 2 ONLY if BOTH (a) the perception/robotics core maps directly to Ed's actual
      background, AND (b) Aleh has actually run the GO-TO-MARKET motion this wedge needs
      (enterprise/industrial B2B closed cold like AB InBev/CDEK, fast paid design partners).
    * Score 1 if the wedge needs domain GTM or credentials NEITHER founder has run
      (e.g. defense procurement, agronomy/farm sales, regulated clinical-lab sales,
      semiconductor fabs) — they can credibly explain the fit but have no unfair access there.
    * Score 0 only if there is truly no reason this team wins.
- problem_intensity: 2 ONLY if a named buyer DEMONSTRABLY already spends money/time on this
  exact task today (cite it). Aspirational pain = 1.
- market_beachhead: 2 ONLY with a concrete $1B+ adjacency map; a single plausible $100M+
  beachhead with hand-wavy expansion = 1.
- business_model: 2 needs payer + price + margin + CAC channel + expansion all named; if any
  is missing or unvalidated = 1.
- why_now / differentiation / sales_cycle: calibrate the same way against the threshold table.
- projected_experiment_targets must reflect THIS wedge's REAL sales cycle. Long
  enterprise / defense / regulated / fab cycles realistically yield FEWER design partners and
  often 0 LOIs in a 4-week sprint; fast SMB / developer-infra / prosumer motions yield more.
  Differentiate the targets per wedge — do not copy the same numbers across wedges."""


def _client():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    return anthropic.Anthropic(api_key=key)


def _extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0] if "```" in text else text
    start = text.find("{")
    end = text.rfind("}")
    return json.loads(text[start:end + 1])


def propose(facts, history, parent=None, directive="", loop_idx=0):
    """One live proposal. Returns the parsed wedge dict + raw token usage."""
    client = _client()

    hist_lines = []
    for h in history:
        hist_lines.append(
            f"- {h['id']} {h['name']}: today={h['today_score']} projected={h['projected_score']} ({h['status']})")
    hist_blob = "\n".join(hist_lines) if hist_lines else "(none yet)"

    parent_blob = ""
    if parent is not None:
        parent_blob = ("\nPARENT WEDGE TO MUTATE (improve its projected score with a "
                       "grounded change — sharper ICP, better-matched RFP, stronger moat, "
                       "or a more decisive 4-week experiment):\n"
                       + json.dumps(parent, indent=2))

    user = f"""SOURCE FACTS (your only allowed grounding):
{json.dumps(facts, indent=2)}

EXPERIMENTS SO FAR:
{hist_blob}
{parent_blob}

DIRECTIVE FOR THIS LOOP #{loop_idx}: {directive}

{SCHEMA_SPEC}"""

    resp = client.messages.create(
        model=MODEL,
        max_tokens=2200,
        system=SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    raw = resp.content[0].text
    wedge = _extract_json(raw)
    usage = {"input_tokens": resp.usage.input_tokens,
             "output_tokens": resp.usage.output_tokens}
    return wedge, usage
