# Pong Academy → Antler Wedge Finder

**Pong Academy** is an agentic, self-improving robotics **calibration loop**: a physical rig
(camera + servo + edge detector) segments real-world attempts into scored *episodes*, exposes
them over an **MCP** server, and a cloud agent — following Karpathy's
[autoresearch](https://github.com/karpathy/autoresearch) contract — rewrites the action policy,
tests the physical shot, and **keeps the change only if the score improves** (else rolls back).
Last-mile calibration for edge devices, powered by autoresearch, available by MCP.

This repo also contains the **wedge finder**: a real autoresearch loop that searched candidate
startup wedges for the technology and scored each against an Antler pre-seed rubric, producing a
single self-contained investor-readable HTML report.

## The loop (same contract, applied to wedges)

`propose (live LLM) → score (FROZEN evaluator) → keep / rollback`

- **Frozen evaluator** — [`engine/evaluator.py`](engine/evaluator.py): a deterministic 0–100
  scorer implementing the exact Antler weights (10 criteria), the 5 hard gates, and the
  weak/min/strong evidence thresholds. It is the immutable contract — the agent can never edit it
  or the weights to inflate a score. Its SHA-256 is recorded in `outputs/loops.json` for audit.
  Today-state traction is floored to reality (no invented customers).
- **Mutable policy** — the wedge spec, filled by a **live Anthropic API call**
  (`claude-sonnet-4-20250514`) in [`engine/propose.py`](engine/propose.py), grounded only in the
  extracted source facts ([`engine/facts.json`](engine/facts.json)).
- **Keep / rollback** — [`engine/run_loop.py`](engine/run_loop.py) runs ~10 logged loops; the
  champion advances only when a proposal's projected score beats it.

Two scores per wedge: **today** (evidence that exists now — harsh) and **projected** (after the
single 4-week validating experiment succeeds).

## Output

- **[`outputs/pong_academy_wedges.html`](outputs/pong_academy_wedges.html)** — one self-contained
  file (inline CSS/JS, video baked in, no CDN, no build step). Open by double-click. Animated hero,
  Top 3 wedges, per-wedge Antler schema + today/projected breakdown + hard gates + the 4-week
  experiment, the full all-loops audit table, and an honest verdict.
- **[`outputs/loops.json`](outputs/loops.json)** — full auditable run log.

### Top 3 wedges (ranked by projected Antler score)

1. **RoboLoop Calibration Infrastructure** (robotics-infra) — today 61.6 → projected 88.3
2. **RoboCell Commissioning AutoLoop** (industrial-arms) — today 61.6 → projected 88.3
3. **LabBot Precision Calibration Loop** (autonomous-labs) — today 56.4 → projected 82.2

**Honest verdict:** no candidate clears the 70+ "potentially fundable" band *today* — Evidence of
Demand sits at the floor and the validation hard gate fails for all, because no paying customer /
pilot / LOI / design partner exists yet. Fastest path across the line: run the #1 wedge's 4-week
experiment.

## Reproduce

```bash
pip install anthropic pdfplumber
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env.local   # not committed
python3 engine/run_loop.py     # 10 live loops -> outputs/loops.json
python3 build_html.py          # render the self-contained HTML
```

## Layout

```
engine/evaluator.py     frozen Antler scorer (immutable contract)
engine/propose.py       live-LLM wedge proposer (Anthropic API)
engine/run_loop.py      the propose->score->keep/rollback loop
engine/facts.json       extracted, citable source facts
engine/assets/          compressed demo video used by the build
build_html.py           renders the single self-contained HTML report
outputs/                the HTML deliverable + loops.json audit log
autoresearch-master/    upstream clone of karpathy/autoresearch (MIT), studied for the contract
*.md                    source/strategy docs (Antler rubric, VC RFPs, seed ideas, architecture)
```

`autoresearch-master/` is an unmodified copy of [karpathy/autoresearch](https://github.com/karpathy/autoresearch) (MIT), included for reference.
