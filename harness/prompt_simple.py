"""harness/prompt_simple.py — assemble the candidate prompt for the simple loop.

No scheduler, no family labels, no parent-diff mode contract. Just: profile +
frozen surface + workspace + goal + one EXPLORE/EXPLOIT directive + the
operator --directive slot + --ban block + parent + inspirations + ledger.
"""
from __future__ import annotations

from harness.archive import ArchiveRecord

_LEDGER_MAX = 40

_EXPLORE_DIRECTIVE = """=== EXPLORE MODE ===
This slot exists to escape the basin the best sits in. DIVERGE — do not refine
the parent with one knob moved; pick a *fundamentally different mechanism* (a
different decoding strategy, segmentation/windowing scheme, a backend return
channel you have not used, or a different error axis to attack). A change that
reads as "the same pipeline, one parameter different" is the WRONG move here."""

_EXPLOIT_DIRECTIVE = """=== EXPLOIT MODE ===
This slot consolidates. Build directly on the parent shown below: tune it
against its dominant error axis, combine it with a complementary approach from
the inspirations, or repair a specific failure. A focused, hypothesis-driven
refinement is exactly right here."""


def _format_parent(parent: ArchiveRecord | None) -> str:
    if parent is None:
        return "(no parent yet — you are writing the first candidate from the stub.)"
    cer = f"{parent.cer:.4f}" if parent.cer is not None else "n/a"
    return (
        f"Parent to build on: id={parent.id} cer={cer} "
        f"fingerprint={','.join(parent.fingerprint)}\n"
        f"hypothesis: {parent.hypothesis}\n"
        f"(the parent's evaluated code is already restored into "
        f"workspace/transcribe.py — edit it.)"
    )


def _format_inspirations(inspr: list[ArchiveRecord]) -> str:
    if not inspr:
        return "(no inspirations yet.)"
    lines = ["Inspirations (other archive entries — combine/learn, do not blindly copy):"]
    for r in inspr:
        cer = f"{r.cer:.4f}" if r.cer is not None else "n/a"
        lines.append(f"- id={r.id} cer={cer} fp={','.join(r.fingerprint)} :: {r.hypothesis}")
    return "\n".join(lines)


def _format_ledger(records: list[ArchiveRecord]) -> str:
    seen: set[str] = set()
    facts: list[str] = []
    for r in reversed(records):  # newest first for dedup
        learned = (r.what_i_learned or "").strip()
        if not learned or learned in seen:
            continue
        seen.add(learned)
        facts.append(f"- ({r.id}) {learned}")
        if len(facts) >= _LEDGER_MAX:
            break
    if not facts:
        return "(no findings recorded yet — you are mapping the surface from scratch.)"
    return "\n".join(reversed(facts))


_RECENT_FAILURES_MAX = 5
_FAILURE_ERR_MAX = 400


def _format_recent_failures(records: list[ArchiveRecord]) -> str:
    """The 'artifacts' channel: show the last N NON-scored records that captured
    an error so the next candidate can map approach -> error and not repeat it.

    Newest first; only emitted when at least one such record exists (caller
    keeps the block out of the prompt entirely otherwise)."""
    entries: list[str] = []
    for r in reversed(records):  # newest first
        if r.status == "scored":
            continue
        err = (r.error or "").strip()
        if not err:
            continue
        gist = (r.hypothesis or "").strip().splitlines()
        gist = gist[0].strip() if gist else ""
        if not gist:
            gist = ",".join(r.fingerprint) or "(no description)"
        err = " ".join(err.split())
        if len(err) > _FAILURE_ERR_MAX:
            err = err[:_FAILURE_ERR_MAX] + "…"
        entries.append(f"- ({r.id}) approach: {gist}\n  error: {err}")
        if len(entries) >= _RECENT_FAILURES_MAX:
            break
    if not entries:
        return ""
    return (
        "=== RECENT FAILURES (do NOT repeat these — fix or avoid) ===\n"
        + "\n".join(entries)
    )


def _format_bans(bans: list[str]) -> str:
    if not bans:
        return ""
    lines = ["=== DO NOT ==="]
    lines += [f"- Do not: {b}" for b in bans]
    return "\n".join(lines)


def build_simple_prompt(
    profile: str,
    frozen_surface: str,
    workspace_body: str,
    baseline: dict,
    mode: str,                 # "EXPLORE" | "EXPLOIT"
    directive: str,
    bans: list[str],
    parent: ArchiveRecord | None,
    inspirations: list[ArchiveRecord],
    allowed_path: str,
    best_cer: float | None,
    best_hyp_id: str | None,
    archive: list[ArchiveRecord] | None = None,
) -> str:
    mode_block = _EXPLORE_DIRECTIVE if mode == "EXPLORE" else _EXPLOIT_DIRECTIVE
    ledger = _format_ledger(archive or (([parent] if parent else []) + inspirations))
    directive_block = (
        f"=== OPERATOR DIRECTIVE ===\n{directive}" if directive.strip()
        else "(no operator directive this run.)"
    )
    bans_block = _format_bans(bans)
    failures_block = _format_recent_failures(archive or [])
    best_line = (
        f"best so far: {best_hyp_id} (cer {best_cer:.4f})"
        if best_cer is not None else "best so far: none yet"
    )
    # First char must not be '-' (argv parsers treat leading -- as a flag).
    return f"""=== BEGIN CANDIDATE PROFILE (harness/prompts/candidate_simple.md) ===
{profile}
=== END CANDIDATE PROFILE ===

Current workspace/transcribe.py (your starting point this iteration):

```python
{workspace_body}
```

Your backend surface — frozen/asr_backend.py (inlined; sandbox denies Read of
frozen/, so THIS is your surface map — study what load() returns and what the
decode call accepts/returns):

```python
{frozen_surface}
```

Goal:
- Lower corpus_cer on the 0715 eval batch (final target <= {baseline.get("target_cer")}).
- Stay within the runtime budget: {baseline.get("total_inference_time_s")} seconds (hard cap enforced).

Hard constraints (also in profile):
- Modify only {allowed_path}.
- Keep transcribe(audio, sr) -> str.
- Do not import ctranslate2/transformers, call from_pretrained, or instantiate Whisper.
- Do not read assets/audio_profile, baseline internals, judge internals, or holdout.
- Make one focused change.

Current state: {best_line}

{mode_block}

{directive_block}

{bans_block}

{_format_parent(parent)}

{_format_inspirations(inspirations)}

{failures_block}

Findings ledger (facts already established — build on them, do not re-derive):
{ledger}

Edit {allowed_path} directly and stop. Emit the required YAML metadata block
(see profile "Required output format") as the LAST thing in your response.
"""
