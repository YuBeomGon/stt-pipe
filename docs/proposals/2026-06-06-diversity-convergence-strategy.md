# Diversity-and-Convergence Strategy for the Flat-Archive Self-Evolve Loop

> Scope: the DIVERSITY/CONVERGENCE section of the radically-simplified loop
> (never-pruned flat archive + per-iteration `claude -p` move). This document
> specifies the minimal mechanism that gives **(b) ensemble support** and
> **(c) no premature convergence** without reintroducing a scheduler. It is
> grounded against the existing judge + harness code; reusable infra is cited
> inline.

## 0. What we are NOT rebuilding

The previous run already *generated* diversity (8+ algorithm families). The flat
never-pruned archive fixes failure **(a)** for free: nothing is abandoned, so a
0.17 lexicon-rerank or a 0.18 ROVER attempt stays available forever as evolution
material. The job here is only to make the loop **cultivate** that material:
keep showing it to the LLM, make ensembling physically possible, and stop one
lineage from eating the budget — all with a handful of operator-legible knobs.

Design bias throughout: the **simplest** thing that demonstrably yields
diverse-exploration → best-convergence. Every mechanism below is < 100 lines and
reads from fields the judge **already computes** (`judge/metrics.py:corpus_aggregate`).

---

## 1. Quality-diversity: flat archive + descriptor-keyed *elite index* (not a MAP-Elites grid)

**Recommendation: a flat never-pruned archive, plus a thin derived "elite index"
that keeps the best entry per descriptor cell.** Do NOT build a MAP-Elites grid
as the *storage* substrate; build it as a *view* over the flat archive.

### Why this and not a real grid

MAP-Elites (Mouret & Clune 2015) earns its keep when storage is the
diversity-preservation mechanism — you keep the best per cell and *discard* the
rest. We must NOT discard (the whole point of the flat archive is that
0.17-0.18 novelty survives to become ensemble fodder, failure (a)). A real grid
also forces premature decisions about bin edges and silently drops the 2nd-best
occupant of a cell — which is often the *diverse* one we want for ensembling.

So: **the archive stays a flat append-only list; the grid is a 20-line function
that buckets the current archive into descriptor cells on demand and returns the
best CER per cell.** This is "MAP-Elites as a query, not a database" — we get the
illumination benefit (one strong representative per region of behaviour space)
for the prompt context (§2) without the pruning cost.

This mirrors what `harness/portfolio.py` already does (axis-keyed "best per
slot" + a near-best pool that is re-filtered, never discarded): keep its
*spirit*, drop its complexity. The new loop replaces `portfolio.py`'s 4 hand-
named axis slots with the descriptor cells below and replaces its policy-
mirroring bookkeeping with a recompute-on-read view.

### The descriptors (all from existing `score_report.json` fields)

Two cheap descriptors, both computable from `corpus_aggregate` output already on
disk per iter (`runs/<hyp_id>/score_report.json`). Source field names verified in
`judge/metrics.py`:

**D1 — dominant error axis** (categorical, 3 bins). Derived exactly as the
existing `harness/runner.py:_dominant_axis()` already classifies it, from
`error_breakdown.{sub_ratio,del_ratio,ins_ratio}`, `length_ratio.mean`, and
`hallucination_hit_rate`:

| bin | rule (reuse `_dominant_axis` thresholds) |
|-----|------------------------------------------|
| `over_generation` | `hallucination_hit_rate ≥ 0.30` **or** `ins_ratio > 0.30` |
| `coverage_deletion` | `del_ratio ≥ sub_ratio` **or** `length_ratio.mean < 0.85` |
| `substitution` | otherwise (sub dominates, coverage healthy) |

**D2 — approach tag** (categorical, ~6-8 bins). The candidate already emits a
`fingerprint` (1-6 lowercase tokens) and an optional `lane`, parsed/validated by
`runner.parse_candidate_metadata`. Use a **coarsening map** from fingerprint
tokens to one of a small fixed vocabulary so the bin count stays bounded and
human-legible:

```
windowing | vad/segmentation | decode_params | ensemble (rover/mbr/medoid/vote)
| lexicon/rerank | frontend/audio | prompt/context | postprocess/script
```

Tag = first vocabulary class any fingerprint token maps to; else `other`. ~20
lines, no model call. (We deliberately do NOT mine the diff — fingerprint is the
contract field built for exactly this dedup/bucketing purpose.)

### Cell = (D1 × D2). Best-per-cell on CER. Coverage = #occupied cells.

A 3 × 8 = 24-cell behaviour space. The elite index = `{(d1,d2): argmin_cer
entry}`. This is < 60 lines total (descriptor functions + a `group_by` over the
flat list). The flat archive keeps everything; the index is recomputed each
iteration from it.

**Why two descriptors, not more**: D1 tells the LLM *which failure region* an
approach lives in (so combine/ensemble picks complementary regions); D2 tells it
*what mechanism family* it is (so it does not propose a 9th windowing variant).
Both are free. A third descriptor (e.g. runtime band from `runtime_s_per_audio_min`)
adds cells without adding decision value at this corpus size (11 files) — skip it
until coverage saturates.

---

## 2. Context-sampling policy for the prompt

The mono-culture failure (c) was partly a *context* failure: the LLM mostly saw
the global best, so it kept refining the global best. Fix: **every prompt shows a
fixed, diverse slate, with few knobs.** Concretely, the archive context block is
the union of four deterministic slices (dedup by hyp_id, cap total at `K≈8`):

1. **Global best** — 1 entry (the incumbent; always shown so exploit can anchor).
2. **Elite-per-cell** — the best entry of every *occupied* descriptor cell from
   §1, sorted by CER, take up to 4. **This is the diversity guarantee**: the LLM
   regularly sees the best coverage_deletion approach, the best ensemble
   approach, the best substitution approach, etc. — not N refinements of one.
3. **Diverse random sample** — `R=2` entries sampled from the flat archive with
   probability weighted *away* from already-shown cells (sample a cell uniformly
   among unshown occupied cells, then a random member). Surfaces buried 2nd-best
   occupants (the ones a real grid would have dropped) for resurrection/ensemble.
4. **Recent** — last `recent_n=3` iters (reuse `runner._recent_iters`), so the
   LLM sees what just failed and why (its error-mix row), avoiding immediate
   repeats. Reuse the existing `_format_recent_table` renderer verbatim.

Each shown entry carries: `hyp_id`, `corpus_cer`, the D1/D2 descriptors, the
one-line `what_i_learned` from the findings ledger (reuse
`runner._format_findings_ledger`), and — for entries the move may build on — the
`candidate.diff` (already stored per iter; cap chars as
`runner._PROMISING_DIFF_MAX_CHARS` does today).

**Knobs (3 total):** `K` (total slate cap, default 8), `R` (random-diverse
count, default 2), `recent_n` (default 3). Everything else is derived. This is a
generalization of the slate the runner *already* assembles (best diagnosis +
promising rejects + recent table + ledger) — we are mostly re-pointing existing
formatters at the descriptor index instead of the ad-hoc `_promising_rejects`
axis-gain heuristic.

> Open-ended-evolution note: this is the **novelty-search** insight (Lehman &
> Stanley 2011) applied to *context selection* rather than to the objective —
> we keep selecting on CER (objective stays simple), but we guarantee the LLM is
> regularly *exposed* to behaviourally-novel elites so it can compose them.

---

## 3. Ensemble enablement (the contract tweak)

This is the one place the single-file contract genuinely blocks the goal.
Ensembles (ROVER/MBR/medoid voting) need ≥2 diverse strong base systems to vote
across, and today a candidate has exactly one file (`workspace/transcribe.py`)
and reaches the model only through `frozen.asr_backend` — it **cannot** run two
archived approaches, because every prior approach's *code* was rolled back on
reject and only its diff survives in `runs/<hyp>/candidate.diff`.

### Simplest mechanism that makes ensembling POSSIBLE: a read-only `base_systems/` snapshot dir + a one-call hook

Keep the evolved artifact a single file. Add a **frozen, read-only library of
selected archived base systems** the candidate may *import and call*:

1. When an iter is **kept** (or is a per-cell elite), the harness copies its
   `workspace/transcribe.py` to `base_systems/<hyp_id>.py` (append-only, never
   pruned — same philosophy as the archive). ~10 lines in the commit path.
   `base_systems/` is added to the candidate's **allowed read** surface and to a
   tiny import shim, but is **never** writable by the candidate (guard: candidate
   may modify only `workspace/transcribe.py`, unchanged).
2. Expose one helper the candidate may call, alongside the existing
   `frozen.asr_backend` trio:
   ```python
   # frozen/base_systems.py  (frozen surface, candidate import-only)
   def list_base_systems() -> list[str]: ...          # hyp_ids available
   def transcribe_with(hyp_id: str, audio, sr) -> str # run an archived system
   ```
   `transcribe_with` imports `base_systems/<hyp_id>.py` and calls its
   `transcribe(audio, sr)`. Each base system already goes through the *same*
   frozen `asr_backend`, so the model stays frozen and the guard against direct
   `ctranslate2`/`transformers` import is unaffected.
3. The candidate's own `transcribe()` can now do, e.g.:
   ```python
   from frozen.base_systems import transcribe_with
   def transcribe(audio, sr):
       hyps = [transcribe_with(h, audio, sr) for h in ("phase3_017_iter_042",
                                                        "phase3_018_iter_007")]
       return rover_vote(hyps)   # candidate writes the ROVER/MBR combiner
   ```

**Which base systems are offered**: the per-cell elites from §1 (diverse by
construction) + global best. The prompt's combine/ensemble mode block lists the
available `hyp_id`s with their D1/D2 descriptors so the LLM picks
*complementary* ones (e.g. one `coverage_deletion`-strong + one
`substitution`-strong) — which is exactly the ≥2-diverse-strong-systems
precondition that was missing.

**Cost honesty**: an ensemble runs K decodes per file, so its runtime is K×. The
judge already measures `runtime_s_per_audio_min`/`avg_rtf` and the runner has
`runtime_hard_multiplier` — ensembles that blow the runtime budget are rejected
by the *existing* guard, no new mechanism needed. This naturally keeps ensembles
honest (a 5-system ROVER that doesn't beat the best on CER-per-second loses).

Why not "just paste two diffs into the prompt and let the LLM merge the source"
(today's `_COMBINE_DIRECTIVE`): that produces *fused* code, not an *ensemble* —
the LLM has to hand-reconstruct both pipelines inline, which is error-prone and
caps you at ~2 small systems. The snapshot-import path lets a candidate vote
across N real, already-validated systems with a few lines, which is the only way
ROVER/MBR pay off.

---

## 4. Convergence vs exploration balance (no scheduler)

Keep the move **LLM-chosen** but bound it with **one predictable guarantee** and
**one operator knob** — no decaying schedule, no diversity-stall detector, no
plateau state machine (all of which the previous harness had and which the
memory notes flag as over-firing).

### The rule: guaranteed explore fraction via a deterministic accumulator

- Operator sets a single knob `explore_fraction` (default `0.30`).
- Each iteration, a deterministic error-diffusion accumulator (reuse
  `runner._is_explore_iter`'s exact RNG-free, resume-safe technique, but with a
  **constant** ratio instead of the decaying one) decides: is this a
  **guaranteed-explore** slot?
  - **Guaranteed-explore slot** → the prompt forces a DIVERGE move targeting an
    **empty or under-occupied descriptor cell** (reuse `_EXPLORE_DIRECTIVE`,
    plus "aim at cell (d1,d2) which the archive has not yet filled"). The LLM
    does not get to choose exploit here.
  - **Otherwise** → the LLM freely chooses tune / combine/ensemble / explore
    from the diverse slate (§2). Even "free" iters see diverse elites, so
    exploit is not forced onto one lineage.

This is the **island-model intuition** (Tanese 1989) compressed to one axis: a
fixed migration/exploration cadence that the local hill-climb cannot override.
It directly prevents both failure modes:

- **(i) chasing novelty forever** — bounded: only `explore_fraction` of iters are
  forced-explore; the rest can consolidate.
- **(ii) collapse onto one lineage** — bounded: the floor of forced-explore at
  empty cells guarantees ≥ `explore_fraction` of the budget keeps opening new
  behaviour regions, and §2 keeps diverse elites in front of the LLM even on
  free iters, so a 74%-on-one-lineage outcome is structurally impossible (a
  lineage can win free iters but never the guaranteed-explore floor).

One number (`explore_fraction`), constant over the run, fully legible: "30% of
iterations are forced to attack an unexplored region; the rest the LLM decides."
No curve to reason about, resume-safe, no RNG.

> We deliberately drop the decaying `EXPLORE_RATIO_START/FLOOR/DECAY` triple.
> The decay existed to force explore→exploit late; the flat archive + diverse
> context (§2) already make late exploitation cheap (elites are always visible),
> so a *constant* floor is sufficient and far simpler to reason about. If
> operators later want late-game consolidation they can lower one number, not
> retune three.

---

## 5. Convergence signal & stopping

Three numbers, all derivable from the flat archive + `score_report.json`s
already on disk; surface them in the per-iter log and a one-line job summary.

1. **Best-CER trajectory** — `min(corpus_cer)` over iters, plus the hyp_id and
   iteration it was set. *Converging* = still dropping; the headline metric.
2. **Archive coverage** — `#occupied descriptor cells / 24` (§1). *Diversity
   health*: rising early (exploration working), plateauing once the space is
   mapped. If coverage is high but best-CER is stuck, the bottleneck is
   *synthesis* (combine/ensemble), not generation — exactly the previous run's
   diagnosis, now made visible.
3. **Stagnation counter** — consecutive *evaluated* iters with no new global best
   beyond the existing noise threshold (`policy` already computes keep/reject vs
   noise floor; reuse it). Reset on any new best.

### Stopping rule (operator-legible, two conditions)

Stop / surface-for-operator when **either**:

- `stagnation_counter ≥ S` (default `S=15`) **and** coverage has not increased
  in the last `S` iters → the loop is neither improving CER nor finding new
  regions: genuinely stuck, escalate. (Distinguishes "stuck-exploiting" from
  "still-illuminating": if coverage is still rising we are not done even at flat
  CER, because new regions may yet yield an ensemble base.)
- Iteration budget exhausted (existing behaviour).

`S` is the only added knob here. A best-CER plot + the coverage fraction +
stagnation counter in the job summary is enough for the operator to read "done /
plateaued / stuck-and-narrow" at a glance.

---

## 6. Summary of added surface (everything is small + reuses existing infra)

| Concern | Mechanism | Lines | Reuses |
|---------|-----------|-------|--------|
| (1) QD | flat archive + recompute-on-read elite index over (D1 dom-axis × D2 approach-tag) | ~60 | `_dominant_axis`, `fingerprint`, `portfolio.py` spirit |
| (2) context | best + per-cell elites + diverse-random + recent, capped K=8 | ~40 | `_recent_iters`, `_format_recent_table`, `_format_findings_ledger`, diff injection |
| (3) ensemble | append-only `base_systems/<hyp>.py` snapshots + `frozen.base_systems.transcribe_with` | ~30 | frozen-surface pattern, runtime guard |
| (4) balance | constant `explore_fraction` via RNG-free accumulator, forced-explore at empty cells | ~25 | `_is_explore_iter` accumulator, `_EXPLORE_DIRECTIVE` |
| (5) stopping | best-CER + coverage fraction + stagnation counter; stop on stuck-and-narrow | ~25 | policy noise-threshold keep/reject |

**Operator knobs, total: 5** — `explore_fraction` (0.30), `K` (8), `R` (2),
`recent_n` (3), `S` (15). No scheduler, no per-mode state machine, no decaying
curves. Each maps to one English sentence.

### Citations (only where they earn it)
- **MAP-Elites** (Mouret & Clune, 2015) — descriptor-cell illumination; used as a
  *view*, not storage (we never prune).
- **Novelty search** (Lehman & Stanley, 2011) — justification for guaranteeing
  the LLM *sees* behaviourally-diverse elites (§2), while the objective stays
  pure CER.
- **Island models** (Tanese, 1989) — the fixed-cadence forced-exploration floor
  (§4) is a one-axis migration guarantee the local hill-climb cannot suppress.
