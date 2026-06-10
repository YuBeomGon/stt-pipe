# Simple Self-Evolve Harness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax.

**Goal:** Build a radically simplified self-evolution harness next to the existing one — a never-pruned flat archive + per-iteration `claude -p` move + verify scoring + keep-if-better — that an operator can understand in one sentence and that supports a built-in `{llm,random,best}` parent-policy ablation on holdout.

**Architecture:** A ~200-line loop (`harness/evolve_simple.py`) that, each iteration, picks a deterministic EXPLORE/EXPLOIT mode, selects a parent + context slate from the flat archive, materializes the parent's `transcribe.py` into the workspace, builds a prompt, runs the hardened candidate CLI, performs a one-line git scope check, runs the existing `run_verify`, **always** appends an archive row, advances `best.txt` only on real improvement, and restores the workspace. Nothing is ever pruned or rolled back from the archive. Reusable pure infra (`judge/evaluate.py`, `harness/verify.py`, `harness/guards.py`) is imported unchanged; the hardening/diff-capture/YAML-parse helpers are **lifted** out of the 2772-line `harness/runner.py` into a small `harness/candidate_cli.py` so the new loop never imports the old controller.

**Tech Stack:** Python 3.11+, stdlib (`subprocess`, `dataclasses`, `pathlib`, `hashlib`, `json`, `random`), `pyyaml` (already a dep), `pytest` run under **base python** (`python -m pytest`, NOT the `evolve` conda env). Evaluation runs in the existing `judge.evaluate` subprocess path unchanged.

---

## Design invariants (do not violate while implementing)

- **DO NOT modify** old controller modules: `scheduler.py`, `lineage.py`, `portfolio.py`, `cooldown.py`, `signature.py`, `promotion.py`, `policy.py`, `gitops.py`, or `harness/runner.py` itself (the lift is a **copy**, runner stays as-is for the legacy path). They are retired later, not in this plan.
- **Reuse unchanged:** `judge/evaluate.py::evaluate_batch`, `harness/verify.py::run_verify` (+ `VerifyConfig`/`VerifyResult`), `harness/guards.py::run_checks` (transitive via `run_verify`), `scripts/evaluate_holdout.py`.
- **Reuse constants (not knobs):** `harness/config.py::RUNTIME_HARD_MULTIPLIER` (7.0), `harness/config.py::KEEP_DELTA_EPS` (0.0001).
- **Archive is append-only and never pruned.** `best.txt` is a 1-line cache derivable from `min(cer)` over scored rows.
- **Operator knobs ≤ 6:** `--iters`, `--directive`, `--explore`, `--ban`/`--pin`, `--holdout-every`, `--parent-policy`.
- Candidate may modify **only** `workspace/transcribe.py`; verify-time output lives under `runs/<id>/`. Scope check is a **delta**: snapshot the out-of-scope dirty set right before the candidate runs, and reject only if the candidate introduces a NEW out-of-scope change. (Absolute "clean tree" is wrong — the operator repo is normally dirty with untracked docs/proposals.)

---

## File Structure

**Created:**

| Path | Responsibility |
|------|----------------|
| `harness/candidate_cli.py` | Lifted, self-contained candidate-CLI layer: `_CLAUDE_HARDENING_ARGS`, `_harden_candidate_cmd`, `run_candidate_command` (hardened `claude -p` + stdout/stderr/diff capture), `parse_candidate_metadata` (YAML block extract + validate), `_check_bypass_in_production` (EVOLVE_NO_HARDEN gate). No import of `harness.runner`. |
| `harness/archive.py` | Flat never-pruned archive: `ArchiveRecord` dataclass + `record_to_row`/`row_from_dict`, `load_archive`, `append_record`, `next_id`, `best_record`/`write_best`/`read_best`, `children_count`, `materialize_parent` (copy a record's `transcribe.py` into the workspace), and `base_systems` snapshot helper stub for the post-MVP ensemble. |
| `harness/select_context.py` | `select_context(archive, K, recent, mode, policy, rng, pinned)` → `(parent, inspirations)`; parent policies `llm`(=weighted_random Boltzmann/child-penalty), `random`(seeded), `best`; plus `top_by_cer`, `diverse_sample` (dedup by fingerprint), `recent_attempts`, `dedup_by_fingerprint`. |
| `harness/prompt_simple.py` | `build_simple_prompt(...)` — assembles candidate_simple.md profile + frozen surface + baseline goal + EXPLORE/EXPLOIT directive + directive slot + bans + parent block + inspirations + learnings ledger. |
| `harness/prompts/candidate_simple.md` | Edited copy of `candidate.md` with the mode-scheduler/family/parent-diff machinery prose removed; YAML output contract preserved verbatim. |
| `harness/evolve_simple.py` | The ~200-line loop: `run_iter(...)` (one iteration end-to-end) + `run_job(...)` (the loop) + `SimpleConfig` dataclass + `_coin`/`_seed_for` deterministic mode + per-iter log writing. |
| `scripts/evolve_simple.py` | Thin CLI entrypoint: argparse for the ≤6 knobs → `SimpleConfig` → `evolve_simple.run_job`. |
| `scripts/archive_summary.py` | Leaderboard CLI: read `archive.jsonl` (+ holdout sidecars) and print in-loop CER and holdout CER side by side. |
| `tests/test_candidate_cli.py` | Tests for the lifted hardening/parse/diff-capture layer. |
| `tests/test_simple_archive.py` | Tests for archive read/write/schema/best/materialize. |
| `tests/test_select_context.py` | Tests for parent policies + context slate + dedup. |
| `tests/test_prompt_simple.py` | Tests for prompt builder (sections present, bans/directive injected, contract preserved). |
| `tests/test_evolve_simple_iter.py` | Tests for `run_iter` glue (scope check, always-append, keep-if-better, state/log write) with a stub candidate cmd. |
| `tests/test_evolve_simple_job.py` | Tests for `run_job` loop + `scripts/evolve_simple.py` argparse wiring. |
| `tests/test_archive_summary.py` | Tests for the leaderboard renderer. |

**Modified:** none of the old modules. (Phase-2 runbook below uses existing `scripts/evaluate_holdout.py` unchanged; the *only* thing that must exist for the holdout anchor is the `<job>_state.json::best_hyp_id`/`best_cer` field, which the new loop writes — see Task 8. That file is new-loop-owned, so no old module is touched.)

---

## Grounding notes (verified against the actual code)

These signatures are copied from the real files so the task code below is accurate.

- `judge/evaluate.py::evaluate_batch(batch: str, transcribe_spec: str, out_path: Path, sample_rate: int = 16000, profile_root: Path | None = None) -> dict[str, Any]`. Writes `out_path` (`score_report.json`), `out_path.parent/per_file.jsonl`, `out_path.parent/diagnosis_report.json`. We do **not** call this directly — `run_verify` does.
- `harness/verify.py::run_verify(config: VerifyConfig) -> VerifyResult`.
  - `VerifyConfig` fields (all keyword, frozen dataclass): `repo_root: Path=Path(".")`, `hyp_id: str="manual"`, `batch: str="AIG_녹취반출_20250715"`, `transcribe: str="workspace.transcribe:transcribe"`, `workspace_file: Path=Path("workspace/transcribe.py")`, `baseline_file: Path=Path("baseline/target_cer.json")`, `runs_dir: Path=Path("runs")`, `runtime_hard_multiplier: float=cfg.RUNTIME_HARD_MULTIPLIER`, `quality_budget_hard: bool=False`, `python_executable: str=sys.executable`.
  - `VerifyResult` fields: `ok: bool`, `hyp_id: str`, `out_dir: Path`, `report: dict|None`, `per_file: list|None`, `stdout: str`, `stderr: str`, `error: str|None`. CER lives at `result.report["corpus_cer"]` when `result.ok`.
  - `run_verify` writes `runs/<hyp_id>/score_report.json` + `per_file.jsonl` (via the judge subprocess) and calls `guards.run_checks` transitively.
- `harness/runner.py` source for the lift (verified line anchors): `_CLAUDE_HARDENING_ARGS` (≈131), `_HARDEN_BYPASS_ENV="EVOLVE_NO_HARDEN_CLAUDE"` (≈147), `_bypass_active`/`_check_bypass_in_production` (≈150/154), `_harden_candidate_cmd` (≈184), `run_candidate_command` (≈1653), `parse_candidate_metadata` (≈952), `_REQUIRED_META_KEYS=("capability_investigated","what_i_learned","hypothesis","fingerprint")` (≈45), `_YAML_FENCE_RE=re.compile(r"```yaml\s*\n(.*?)\n```", re.DOTALL)` (≈60), `_FINGERPRINT_MAX_TOKENS=6` (≈61), `_run_git` (≈294). Prompt formatters to mirror: `_recent_iters` (≈805), `_format_recent_table` (≈872), `_format_findings_ledger` (≈929), `_load_profile`/`_load_workspace_body`/`_load_frozen_surface` (≈758/771/788).
- `harness/config.py`: `RUNTIME_HARD_MULTIPLIER=7.0`, `KEEP_DELTA_EPS=0.0001`.
- `scripts/evaluate_holdout.py::_best_eval_run(runs_dir, summary_dir, job_id)` reads `summary_dir/<job_id>_state.json::best_hyp_id` then checks `runs/<best_hyp_id>/score_report.json` — this is the **only** coupling: the new loop must write that state file.

---

## Task 1 — Lift candidate CLI into `harness/candidate_cli.py`

Self-contained module: hardened `claude -p` invocation, stdout/stderr/diff capture, YAML metadata parse, production-bypass gate. **No import of `harness.runner`.** This is a copy/lift — runner.py is untouched.

**Files**
- Create: `harness/candidate_cli.py`
- Test: `tests/test_candidate_cli.py`

**Steps**

- [ ] Write failing test `tests/test_candidate_cli.py`:
  ```python
  """Tests for the lifted candidate-CLI layer (harness/candidate_cli.py)."""
  from __future__ import annotations

  import os
  import sys
  from pathlib import Path

  import pytest

  ROOT = Path(__file__).resolve().parents[1]
  if str(ROOT) not in sys.path:
      sys.path.insert(0, str(ROOT))

  from harness import candidate_cli as cc


  def test_harden_injects_flags_for_claude():
      hardened, added = cc.harden_candidate_cmd("claude -p")
      assert "--disallowedTools=Bash,WebFetch,WebSearch,Task" in hardened
      assert "--disable-slash-commands" in hardened
      assert "--strict-mcp-config" in hardened
      assert added  # non-empty list of what was added


  def test_harden_is_idempotent():
      once, _ = cc.harden_candidate_cmd("claude -p")
      twice, added2 = cc.harden_candidate_cmd(once)
      assert twice == once
      assert added2 == []


  def test_harden_passes_through_non_claude():
      cmd = "python my_stub.py"
      hardened, added = cc.harden_candidate_cmd(cmd)
      assert hardened == cmd
      assert added == []


  def test_parse_metadata_happy_path(tmp_path):
      stdout = (
          "blah blah\n```yaml\n"
          "capability_investigated: |\n  studied generate kwargs\n"
          "what_i_learned: |\n  beam returns logprobs\n"
          "hypothesis: |\n  use logprobs to gate\n"
          "fingerprint: [decode, gate]\n```\n"
      )
      meta, reason = cc.parse_candidate_metadata(stdout, out_dir=tmp_path)
      assert reason is None
      assert meta["fingerprint"] == ["decode", "gate"]
      assert (tmp_path / "candidate_meta.json").is_file()


  def test_parse_metadata_rejects_missing_block(tmp_path):
      meta, reason = cc.parse_candidate_metadata("no yaml here", out_dir=tmp_path)
      assert meta is None
      assert "yaml" in reason.lower()
      assert (tmp_path / "candidate_meta.err").is_file()


  def test_run_candidate_command_captures_diff(tmp_path, monkeypatch):
      # Build a tiny git repo with a workspace file a stub command will edit.
      import subprocess
      repo = tmp_path
      subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
      subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
      subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
      ws = repo / "workspace"
      ws.mkdir()
      (ws / "transcribe.py").write_text("x = 1\n")
      subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
      subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
      # Stub candidate: a python script that appends a line to the workspace file.
      stub = repo / "stub.py"
      stub.write_text(
          "import sys, pathlib\n"
          "p = pathlib.Path('workspace/transcribe.py')\n"
          "p.write_text(p.read_text() + 'y = 2\\n')\n"
          "print('```yaml\\ncapability_investigated: a\\nwhat_i_learned: b\\n"
          "hypothesis: c\\nfingerprint: [t]\\n```')\n"
      )
      out_dir = repo / "runs" / "iter1"
      monkeypatch.setenv("EVOLVE_NO_HARDEN_CLAUDE", "1")  # stub isn't claude anyway
      res = cc.run_candidate_command(
          candidate_cmd=f"{sys.executable} {stub}",
          prompt="hello",
          out_dir=out_dir,
          repo_root=repo,
      )
      assert res.returncode == 0
      assert (out_dir / "prompt.md").read_text() == "hello"
      assert (out_dir / "claude_stdout.txt").is_file()
      diff = (out_dir / "candidate.diff").read_text()
      assert "y = 2" in diff
  ```
- [ ] Run `python -m pytest tests/test_candidate_cli.py -q` → expect failure (module missing).
- [ ] Implement `harness/candidate_cli.py` (lift verbatim from runner, public-renamed `harden_candidate_cmd`):
  ```python
  """harness/candidate_cli.py — self-contained candidate-CLI layer.

  Lifted from harness/runner.py (hardening + diff-capture + YAML parse) so the
  simple evolve loop never imports the 2772-line controller. runner.py is left
  unchanged for the legacy path; this is a copy, not a move.
  """
  from __future__ import annotations

  import json
  import os
  import re
  import shlex
  import subprocess
  import sys
  from pathlib import Path
  from typing import Any

  import yaml

  _CLAUDE_HARDENING_ARGS: tuple[str, ...] = (
      "--disable-slash-commands",
      "--strict-mcp-config",
      "--disallowedTools=Bash,WebFetch,WebSearch,Task",
  )
  _HARDEN_BYPASS_ENV = "EVOLVE_NO_HARDEN_CLAUDE"

  _REQUIRED_META_KEYS = (
      "capability_investigated",
      "what_i_learned",
      "hypothesis",
      "fingerprint",
  )
  _YAML_FENCE_RE = re.compile(r"```yaml\s*\n(.*?)\n```", re.DOTALL)
  _FINGERPRINT_MAX_TOKENS = 6


  def _bypass_active() -> bool:
      return os.environ.get(_HARDEN_BYPASS_ENV) == "1"


  def check_bypass_in_production(iterations: int, commit_results: bool) -> None:
      """Refuse the hardening bypass for production jobs (--iters>1 or commit)."""
      if not _bypass_active():
          return
      production = iterations > 1 or commit_results
      if not production:
          print(
              f"WARNING: {_HARDEN_BYPASS_ENV}=1 — candidate hardening bypassed "
              "(skills/MCP exposed); allowed because single-iter no-commit.",
              file=sys.stderr,
          )
          return
      raise RuntimeError(
          f"{_HARDEN_BYPASS_ENV}=1 set but this is a production job "
          f"(iterations={iterations}, commit_results={commit_results}). "
          f"Bypass is debug-only. Unset {_HARDEN_BYPASS_ENV} and retry."
      )


  def harden_candidate_cmd(candidate_cmd: str) -> tuple[str, list[str]]:
      """Inject skills/MCP/Tool hardening flags when argv[0] basename is `claude`.

      Idempotent; non-claude commands pass through unchanged.
      """
      if _bypass_active():
          print(
              f"WARNING: {_HARDEN_BYPASS_ENV}=1 — hardening skipped for this "
              "candidate invocation",
              file=sys.stderr,
          )
          return candidate_cmd, []
      parts = shlex.split(candidate_cmd)
      if not parts or Path(parts[0]).name != "claude":
          return candidate_cmd, []
      added: list[str] = []
      for arg in _CLAUDE_HARDENING_ARGS:
          flag_name = arg.split("=", 1)[0]
          present = any(p == flag_name or p.startswith(flag_name + "=") for p in parts)
          if not present:
              parts.append(arg)
              added.append(arg)
      return shlex.join(parts), added


  def _run_git(repo_root: Path, args: list[str], check: bool = True):
      return subprocess.run(
          ["git", *args], cwd=repo_root, check=check, capture_output=True, text=True
      )


  def run_candidate_command(
      candidate_cmd: str,
      prompt: str,
      out_dir: Path,
      repo_root: Path,
      workspace_file: Path = Path("workspace/transcribe.py"),
  ) -> subprocess.CompletedProcess[str]:
      out_dir.mkdir(parents=True, exist_ok=True)
      (out_dir / "prompt.md").write_text(prompt, encoding="utf-8")
      hardened_cmd, added_flags = harden_candidate_cmd(candidate_cmd)
      if added_flags:
          (out_dir / "candidate_cmd_hardening.txt").write_text(
              f"original: {candidate_cmd}\nhardened: {hardened_cmd}\n"
              f"added:    {' '.join(added_flags)}\n",
              encoding="utf-8",
          )
      cmd = [*shlex.split(hardened_cmd), prompt]
      result = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True)
      (out_dir / "claude_stdout.txt").write_text(result.stdout, encoding="utf-8")
      (out_dir / "claude_stderr.txt").write_text(result.stderr, encoding="utf-8")
      diff = _run_git(repo_root, ["diff", "--", workspace_file.as_posix()], check=False)
      (out_dir / "candidate.diff").write_text(diff.stdout, encoding="utf-8")
      return result


  def parse_candidate_metadata(
      stdout_text: str, out_dir: Path | None = None
  ) -> tuple[dict[str, Any] | None, str | None]:
      """Extract + validate the required YAML metadata block. Returns
      (meta, None) on success or (None, reason) on any failure."""
      def _fail(reason: str):
          if out_dir is not None:
              (out_dir / "candidate_meta.err").write_text(reason, encoding="utf-8")
          return None, reason

      matches = _YAML_FENCE_RE.findall(stdout_text)
      if not matches:
          return _fail("no yaml fenced block in stdout")
      try:
          parsed = yaml.safe_load(matches[-1])
      except yaml.YAMLError as exc:
          return _fail(f"yaml parse failed: {exc}")
      if not isinstance(parsed, dict):
          return _fail(f"yaml block is not a mapping ({type(parsed).__name__})")
      missing = [k for k in _REQUIRED_META_KEYS if k not in parsed]
      if missing:
          return _fail(f"missing keys: {missing}")
      fp = parsed["fingerprint"]
      if not isinstance(fp, list) or not all(isinstance(t, str) for t in fp):
          return _fail(f"fingerprint must be a list of strings, got {type(fp).__name__}")
      if not (1 <= len(fp) <= _FINGERPRINT_MAX_TOKENS):
          return _fail(f"fingerprint length {len(fp)} out of range [1, {_FINGERPRINT_MAX_TOKENS}]")
      normalized_fp = [t.strip().lower() for t in fp]
      if any(not t for t in normalized_fp):
          return _fail("fingerprint contains empty/whitespace-only tokens")
      text_fields: dict[str, str] = {}
      for key in ("capability_investigated", "what_i_learned", "hypothesis"):
          val = parsed[key]
          if not isinstance(val, str) or not val.strip():
              return _fail(f"{key} must be a non-empty string")
          text_fields[key] = val.strip()
      normalized = {**text_fields, "fingerprint": normalized_fp}
      lane = parsed.get("lane")
      if isinstance(lane, str) and lane.strip():
          normalized["lane"] = lane.strip().lower()
      if out_dir is not None:
          (out_dir / "candidate_meta.json").write_text(
              json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8"
          )
      return normalized, None
  ```
- [ ] Run `python -m pytest tests/test_candidate_cli.py -q` → expect pass.
- [ ] Commit:
  ```
  feat(simple-evolve): lift candidate CLI into harness/candidate_cli.py

  Self-contained hardening + diff-capture + YAML-parse layer copied from
  runner.py so the new loop never imports the legacy controller.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```

---

## Task 2 — Flat archive read/write + record schema (`harness/archive.py`)

The never-pruned `runs/<job>/archive.jsonl` (1 row/candidate), `best.txt`, and the per-candidate dir layout. Nothing is ever deleted.

**Files**
- Create: `harness/archive.py`
- Test: `tests/test_simple_archive.py`

**Steps**

- [ ] Write failing test `tests/test_simple_archive.py`:
  ```python
  from __future__ import annotations

  import sys
  from pathlib import Path

  ROOT = Path(__file__).resolve().parents[1]
  if str(ROOT) not in sys.path:
      sys.path.insert(0, str(ROOT))

  from harness import archive as arch


  def _rec(cid, cer, status="scored", parents=None, fp=None):
      return arch.ArchiveRecord(
          id=cid, parents=parents or [], cer=cer, status=status,
          hypothesis="h", what_i_learned="l",
          fingerprint=fp or ["t"], score_report=f"{cid}/score_report.json",
          ts="2026-06-06T00:00:00Z",
      )


  def test_next_id_starts_at_zero_and_increments(tmp_path):
      assert arch.next_id([]) == "0000"
      recs = [_rec("0000", 0.2), _rec("0001", 0.19)]
      assert arch.next_id(recs) == "0002"


  def test_append_and_load_roundtrip(tmp_path):
      job_dir = tmp_path / "runs" / "job"
      arch.append_record(job_dir, _rec("0000", 0.2))
      arch.append_record(job_dir, _rec("0001", 0.18))
      loaded = arch.load_archive(job_dir)
      assert [r.id for r in loaded] == ["0000", "0001"]
      assert loaded[1].cer == 0.18


  def test_load_missing_archive_is_empty(tmp_path):
      assert arch.load_archive(tmp_path / "runs" / "nope") == []


  def test_best_record_ignores_unscored_and_none(tmp_path):
      recs = [
          _rec("0000", 0.2),
          _rec("0001", None, status="rejected"),
          _rec("0002", 0.15),
          _rec("0003", 0.16),
      ]
      best = arch.best_record(recs)
      assert best.id == "0002"


  def test_write_and_read_best(tmp_path):
      job_dir = tmp_path / "runs" / "job"
      job_dir.mkdir(parents=True)
      arch.write_best(job_dir, "0002")
      assert arch.read_best(job_dir) == "0002"


  def test_children_count(tmp_path):
      recs = [_rec("0000", 0.2), _rec("0001", 0.19, parents=["0000"]),
              _rec("0002", 0.18, parents=["0000"])]
      assert arch.children_count(recs, "0000") == 2
      assert arch.children_count(recs, "0001") == 0


  def test_materialize_parent_copies_transcribe(tmp_path):
      job_dir = tmp_path / "runs" / "job"
      (job_dir / "0000").mkdir(parents=True)
      (job_dir / "0000" / "transcribe.py").write_text("def transcribe(a, s):\n    return 'X'\n")
      ws = tmp_path / "workspace" / "transcribe.py"
      ws.parent.mkdir(parents=True)
      arch.materialize_parent(job_dir, "0000", ws)
      assert "return 'X'" in ws.read_text()
  ```
- [ ] Run `python -m pytest tests/test_simple_archive.py -q` → expect failure.
- [ ] Implement `harness/archive.py`:
  ```python
  """harness/archive.py — flat, never-pruned candidate archive.

  Layout (the entire state of the loop):
    runs/<job>/
      archive.jsonl   # append-only, 1 row/candidate, NEVER pruned
      best.txt        # 1-line cache of best id (derivable from min cer)
      <id>/transcribe.py, candidate.diff, score_report.json, prompt.md,
           claude_stdout.txt
  """
  from __future__ import annotations

  import json
  import shutil
  from dataclasses import asdict, dataclass, field
  from pathlib import Path
  from typing import Any


  @dataclass(frozen=True)
  class ArchiveRecord:
      id: str
      parents: list[str]
      cer: float | None
      status: str  # "scored" | "rejected" | "format_reject" | "scope_reject" | "command_failed"
      hypothesis: str
      what_i_learned: str
      fingerprint: list[str]
      score_report: str | None
      ts: str
      mode: str | None = None
      lane: str | None = None
      capability_investigated: str = ""
      extra: dict[str, Any] = field(default_factory=dict)


  def record_to_row(rec: ArchiveRecord) -> str:
      return json.dumps(asdict(rec), ensure_ascii=False)


  def row_from_dict(d: dict[str, Any]) -> ArchiveRecord:
      known = {f for f in ArchiveRecord.__dataclass_fields__}
      base = {k: v for k, v in d.items() if k in known}
      extra = {k: v for k, v in d.items() if k not in known}
      base.setdefault("extra", {})
      base["extra"].update(extra)
      return ArchiveRecord(**base)


  def _archive_path(job_dir: Path) -> Path:
      return Path(job_dir) / "archive.jsonl"


  def load_archive(job_dir: Path) -> list[ArchiveRecord]:
      path = _archive_path(job_dir)
      if not path.is_file():
          return []
      out: list[ArchiveRecord] = []
      for line in path.read_text(encoding="utf-8").splitlines():
          line = line.strip()
          if not line:
              continue
          try:
              out.append(row_from_dict(json.loads(line)))
          except (json.JSONDecodeError, TypeError):
              continue  # never crash the loop on a malformed row
      return out


  def append_record(job_dir: Path, rec: ArchiveRecord) -> None:
      job_dir = Path(job_dir)
      job_dir.mkdir(parents=True, exist_ok=True)
      with _archive_path(job_dir).open("a", encoding="utf-8") as fh:
          fh.write(record_to_row(rec) + "\n")


  def next_id(archive: list[ArchiveRecord]) -> str:
      return f"{len(archive):04d}"


  def _scored(archive: list[ArchiveRecord]) -> list[ArchiveRecord]:
      return [r for r in archive if r.status == "scored" and r.cer is not None]


  def best_record(archive: list[ArchiveRecord]) -> ArchiveRecord | None:
      scored = _scored(archive)
      return min(scored, key=lambda r: r.cer) if scored else None


  def write_best(job_dir: Path, hyp_id: str) -> None:
      Path(job_dir).mkdir(parents=True, exist_ok=True)
      (Path(job_dir) / "best.txt").write_text(hyp_id + "\n", encoding="utf-8")


  def read_best(job_dir: Path) -> str | None:
      path = Path(job_dir) / "best.txt"
      return path.read_text(encoding="utf-8").strip() if path.is_file() else None


  def children_count(archive: list[ArchiveRecord], hyp_id: str) -> int:
      return sum(1 for r in archive if hyp_id in r.parents)


  def materialize_parent(job_dir: Path, parent_id: str, workspace_file: Path) -> None:
      """Copy the parent's evaluated transcribe.py into the workspace so the LLM
      edits the real parent code (not a prose reconstruction)."""
      src = Path(job_dir) / parent_id / "transcribe.py"
      if not src.is_file():
          raise FileNotFoundError(f"parent transcribe missing: {src}")
      Path(workspace_file).parent.mkdir(parents=True, exist_ok=True)
      shutil.copyfile(src, workspace_file)


  def snapshot_candidate(job_dir: Path, hyp_id: str, workspace_file: Path) -> None:
      """Persist the evaluated workspace into runs/<job>/<id>/transcribe.py so it
      can later be materialized as a parent. Always called for scored iters."""
      dst = Path(job_dir) / hyp_id / "transcribe.py"
      dst.parent.mkdir(parents=True, exist_ok=True)
      shutil.copyfile(workspace_file, dst)
  ```
- [ ] Run `python -m pytest tests/test_simple_archive.py -q` → expect pass.
- [ ] Commit:
  ```
  feat(simple-evolve): flat never-pruned archive (harness/archive.py)

  ArchiveRecord schema + append-only jsonl + best.txt cache + parent
  materialization. Nothing is ever deleted or rolled back from the archive.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```

---

## Task 3 — `select_context` + parent policies (`harness/select_context.py`)

Parent + inspiration slate from the flat archive. Policies `llm`(=weighted_random Boltzmann/child-penalty), `random`(seeded), `best`. Context = parent + top-3-by-cer + 2-diverse (dedup by fingerprint) + recent N + (learnings handled in the prompt builder).

**Files**
- Create: `harness/select_context.py`
- Test: `tests/test_select_context.py`

**Steps**

- [ ] Write failing test `tests/test_select_context.py`:
  ```python
  from __future__ import annotations

  import random
  import sys
  from pathlib import Path

  ROOT = Path(__file__).resolve().parents[1]
  if str(ROOT) not in sys.path:
      sys.path.insert(0, str(ROOT))

  from harness import archive as arch
  from harness import select_context as sc


  def _rec(cid, cer, parents=None, fp=None):
      return arch.ArchiveRecord(
          id=cid, parents=parents or [], cer=cer, status="scored",
          hypothesis="h", what_i_learned="l", fingerprint=fp or [cid],
          score_report=None, ts="t",
      )


  def _archive():
      return [_rec(f"{i:04d}", 0.30 - i * 0.01, fp=[f"f{i % 3}"]) for i in range(6)]


  def test_policy_best_returns_min_cer():
      a = _archive()
      parent, _ = sc.select_context(a, K=8, recent=3, mode="EXPLOIT",
                                    policy="best", rng=random.Random(0))
      assert parent.id == arch.best_record(a).id


  def test_policy_random_is_seed_deterministic():
      a = _archive()
      p1, _ = sc.select_context(a, K=8, recent=3, mode="EXPLORE",
                                policy="random", rng=random.Random(7))
      p2, _ = sc.select_context(a, K=8, recent=3, mode="EXPLORE",
                                policy="random", rng=random.Random(7))
      assert p1.id == p2.id


  def test_policy_llm_exploit_uses_best():
      a = _archive()
      parent, _ = sc.select_context(a, K=8, recent=3, mode="EXPLOIT",
                                    policy="llm", rng=random.Random(0))
      assert parent.id == arch.best_record(a).id


  def test_pinned_overrides_parent_in_exploit():
      a = _archive()
      parent, _ = sc.select_context(a, K=8, recent=3, mode="EXPLOIT",
                                    policy="best", rng=random.Random(0),
                                    pinned="0002")
      assert parent.id == "0002"


  def test_inspirations_dedup_by_fingerprint_and_capped():
      a = _archive()
      _, inspr = sc.select_context(a, K=4, recent=2, mode="EXPLORE",
                                   policy="llm", rng=random.Random(1))
      seen_fp = [tuple(r.fingerprint) for r in inspr]
      assert len(seen_fp) == len(set(seen_fp))  # deduped
      assert len(inspr) <= 4


  def test_empty_archive_returns_none_parent():
      parent, inspr = sc.select_context([], K=8, recent=3, mode="EXPLORE",
                                        policy="llm", rng=random.Random(0))
      assert parent is None
      assert inspr == []
  ```
- [ ] Run `python -m pytest tests/test_select_context.py -q` → expect failure.
- [ ] Implement `harness/select_context.py`:
  ```python
  """harness/select_context.py — parent + inspiration slate from the flat archive.

  Diversity/exploration is an emergent property of stochastic sampling over the
  never-pruned archive, NOT a scheduler. Three parent policies for the ablation:
    best   — always the global best (pure hill-climb control)
    random — seeded RNG over scored records (control)
    llm    — weighted_random: Boltzmann perf-weight / (1 + children) (treatment).
             EXPLOIT mode anchors on best; EXPLORE mode samples by weight.
  """
  from __future__ import annotations

  import math
  import random

  from harness import archive as arch
  from harness.archive import ArchiveRecord

  _T = 0.05  # Boltzmann temperature (internal constant, not an operator knob)


  def _scored(archive: list[ArchiveRecord]) -> list[ArchiveRecord]:
      return [r for r in archive if r.status == "scored" and r.cer is not None]


  def _norm_scores(scored: list[ArchiveRecord]) -> dict[str, float]:
      cers = [r.cer for r in scored]
      lo, hi = min(cers), max(cers)
      span = (hi - lo) or 1.0
      # lower cer -> higher score in [0, 1]
      return {r.id: (hi - r.cer) / span for r in scored}


  def _weighted_random(
      archive: list[ArchiveRecord], rng: random.Random
  ) -> ArchiveRecord | None:
      scored = _scored(archive)
      if not scored:
          return None
      norm = _norm_scores(scored)
      weights = [
          math.exp(norm[r.id] / _T) / (1 + arch.children_count(archive, r.id))
          for r in scored
      ]
      return rng.choices(scored, weights=weights, k=1)[0]


  def top_by_cer(archive: list[ArchiveRecord], n: int) -> list[ArchiveRecord]:
      return sorted(_scored(archive), key=lambda r: r.cer)[:n]


  def diverse_sample(
      archive: list[ArchiveRecord], n: int, exclude_fp: set[tuple[str, ...]],
      rng: random.Random,
  ) -> list[ArchiveRecord]:
      pool = [r for r in _scored(archive) if tuple(r.fingerprint) not in exclude_fp]
      rng.shuffle(pool)
      out: list[ArchiveRecord] = []
      seen = set(exclude_fp)
      for r in pool:
          fp = tuple(r.fingerprint)
          if fp in seen:
              continue
          seen.add(fp)
          out.append(r)
          if len(out) >= n:
              break
      return out


  def recent_attempts(archive: list[ArchiveRecord], n: int) -> list[ArchiveRecord]:
      return archive[-n:] if n > 0 else []


  def dedup_by_fingerprint(records: list[ArchiveRecord]) -> list[ArchiveRecord]:
      seen: set[tuple[str, ...]] = set()
      out: list[ArchiveRecord] = []
      for r in records:
          fp = tuple(r.fingerprint)
          if fp in seen:
              continue
          seen.add(fp)
          out.append(r)
      return out


  def select_context(
      archive: list[ArchiveRecord],
      K: int,
      recent: int,
      mode: str,                 # "EXPLORE" | "EXPLOIT"
      policy: str,               # "llm" | "random" | "best"
      rng: random.Random,
      pinned: str | None = None,
  ) -> tuple[ArchiveRecord | None, list[ArchiveRecord]]:
      scored = _scored(archive)
      if not scored:
          return None, []

      # --- parent ---
      if pinned and mode == "EXPLOIT":
          parent = next((r for r in archive if r.id == pinned), arch.best_record(archive))
      elif policy == "best":
          parent = arch.best_record(archive)
      elif policy == "random":
          parent = rng.choice(scored)
      else:  # "llm"
          parent = arch.best_record(archive) if mode == "EXPLOIT" else _weighted_random(archive, rng)

      # --- inspirations: top-3 + 2-diverse + recent N, deduped, capped at K ---
      top = top_by_cer(archive, 3)
      shown_fp = {tuple(r.fingerprint) for r in top}
      div = diverse_sample(archive, 2, exclude_fp=shown_fp, rng=rng)
      rec = recent_attempts(archive, recent)
      inspr = dedup_by_fingerprint([*top, *div, *rec])
      if parent is not None:
          inspr = [r for r in inspr if r.id != parent.id]
      return parent, inspr[:K]
  ```
- [ ] Run `python -m pytest tests/test_select_context.py -q` → expect pass.
- [ ] Commit:
  ```
  feat(simple-evolve): select_context + parent policies (llm/random/best)

  Stochastic parent sampling over the flat archive; diverse inspiration slate.
  llm = Boltzmann weighted_random with child penalty; random/best are controls.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```

---

## Task 4 — Prompt builder + `candidate_simple.md`

Assemble the prompt from the simple profile + frozen surface + baseline goal + EXPLORE/EXPLOIT directive + the `--directive` slot + `--ban` block + parent block + inspirations + learnings ledger (last 40, deduped).

**Files**
- Create: `harness/prompt_simple.py`
- Create: `harness/prompts/candidate_simple.md`
- Test: `tests/test_prompt_simple.py`

**Steps**

- [ ] Create `harness/prompts/candidate_simple.md` — copy `harness/prompts/candidate.md`, then DELETE the mode/family/parent-diff machinery. Concretely:
  - Remove the paragraph "The harness picks a **mode** for each iteration and injects a dedicated mode block …" through "build on its code." (the per-iter mode-block contract).
  - Remove the entire "The harness groups candidates into **families** …" paragraph.
  - Remove "The job is explore-heavy early and keeps a guaranteed floor of exploration throughout …" (decaying-schedule prose).
  - In "Output emission order", change the rollback sentence to: "If verification passes and the result improves on the best, `best.txt` advances; either way your candidate and its `what_i_learned` are kept forever in the flat archive."
  - **Preserve verbatim**: Role, Hard constraints, "Your real surface is undermapped", the reconnaissance list, and the entire **Required output format** YAML contract (`capability_investigated`/`what_i_learned`/`hypothesis`/`fingerprint`, 1–6 tokens) — `parse_candidate_metadata` depends on it unchanged.
- [ ] Write failing test `tests/test_prompt_simple.py`:
  ```python
  from __future__ import annotations

  import sys
  from pathlib import Path

  ROOT = Path(__file__).resolve().parents[1]
  if str(ROOT) not in sys.path:
      sys.path.insert(0, str(ROOT))

  from harness import archive as arch
  from harness import prompt_simple as ps


  def _rec(cid, cer, fp, learned="learned-thing"):
      return arch.ArchiveRecord(
          id=cid, parents=[], cer=cer, status="scored", hypothesis="h",
          what_i_learned=learned, fingerprint=fp, score_report=None, ts="t",
      )


  def test_candidate_simple_md_exists_and_keeps_contract():
      body = (ROOT / "harness" / "prompts" / "candidate_simple.md").read_text()
      assert "Required output format" in body
      assert "fingerprint" in body
      # mode/family machinery removed
      assert "injects a dedicated mode block" not in body
      assert "groups candidates into **families**" not in body


  def test_build_prompt_contains_all_sections():
      parent = _rec("0001", 0.18, ["vad"])
      inspr = [_rec("0002", 0.17, ["rover"], learned="rover helps"),
               _rec("0003", 0.19, ["beam"])]
      prompt = ps.build_simple_prompt(
          profile="PROFILE-BODY",
          frozen_surface="FROZEN-SURFACE",
          workspace_body="def transcribe(a, s): return ''",
          baseline={"target_cer": 0.10, "total_inference_time_s": 152.3},
          mode="EXPLORE",
          directive="try VAD segmentation",
          bans=["beam sweep"],
          parent=parent,
          inspirations=inspr,
          allowed_path="workspace/transcribe.py",
          best_cer=0.17, best_hyp_id="0002",
      )
      assert "PROFILE-BODY" in prompt
      assert "FROZEN-SURFACE" in prompt
      assert "try VAD segmentation" in prompt          # directive slot
      assert "beam sweep" in prompt                     # ban block
      assert "EXPLORE" in prompt                        # mode directive
      assert "0001" in prompt and "0.18" in prompt      # parent block
      assert "rover helps" in prompt                    # learnings ledger
      assert not prompt.startswith("-")                 # argv-safe first char


  def test_build_prompt_exploit_directive_differs():
      explore = ps.build_simple_prompt(
          profile="P", frozen_surface="F", workspace_body="w",
          baseline={"target_cer": 0.1, "total_inference_time_s": 1.0},
          mode="EXPLORE", directive="", bans=[], parent=None, inspirations=[],
          allowed_path="workspace/transcribe.py", best_cer=None, best_hyp_id=None,
      )
      exploit = ps.build_simple_prompt(
          profile="P", frozen_surface="F", workspace_body="w",
          baseline={"target_cer": 0.1, "total_inference_time_s": 1.0},
          mode="EXPLOIT", directive="", bans=[], parent=None, inspirations=[],
          allowed_path="workspace/transcribe.py", best_cer=None, best_hyp_id=None,
      )
      assert "EXPLORE MODE" in explore
      assert "EXPLOIT MODE" in exploit
  ```
- [ ] Run `python -m pytest tests/test_prompt_simple.py -q` → expect failure.
- [ ] Implement `harness/prompt_simple.py`:
  ```python
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
      ledger = _format_ledger(archive or ([parent] if parent else []) + inspirations)
      directive_block = (
          f"=== OPERATOR DIRECTIVE ===\n{directive}" if directive.strip()
          else "(no operator directive this run.)"
      )
      bans_block = _format_bans(bans)
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

  Findings ledger (facts already established — build on them, do not re-derive):
  {ledger}

  Edit {allowed_path} directly and stop. Emit the required YAML metadata block
  (see profile "Required output format") as the LAST thing in your response.
  """
  ```
- [ ] Run `python -m pytest tests/test_prompt_simple.py -q` → expect pass.
- [ ] Commit:
  ```
  feat(simple-evolve): prompt builder + candidate_simple.md profile

  Mode/family/parent-diff machinery removed; YAML output contract preserved.
  EXPLORE/EXPLOIT directive + operator --directive slot + --ban block.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```

---

## Task 5 — `run_iter` glue (`harness/evolve_simple.py`, part 1)

One iteration end-to-end: deterministic mode → select_context → materialize parent → build prompt → run candidate → scope check → run_verify → ALWAYS append archive row → advance best.txt only if improved → restore workspace + write state/log. Old controller modules are NOT imported.

**Files**
- Create: `harness/evolve_simple.py` (this task adds `SimpleConfig`, `_seed_for`, `_coin`, `_out_of_scope`, `_scope_ok`, `run_iter`, `_write_state`, `_write_log`)
- Test: `tests/test_evolve_simple_iter.py`

**Steps**

- [ ] Write failing test `tests/test_evolve_simple_iter.py`:
  ```python
  from __future__ import annotations

  import json
  import subprocess
  import sys
  from pathlib import Path

  ROOT = Path(__file__).resolve().parents[1]
  if str(ROOT) not in sys.path:
      sys.path.insert(0, str(ROOT))

  from harness import archive as arch
  from harness import evolve_simple as es


  def _git_repo(tmp_path: Path) -> Path:
      subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
      subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
      subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
      ws = tmp_path / "workspace"
      ws.mkdir()
      (ws / "transcribe.py").write_text("def transcribe(a, s):\n    return ''\n")
      subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
      subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
      return tmp_path


  def _coin_is_deterministic():
      assert es._coin("job", 5) == es._coin("job", 5)


  def test_coin_deterministic_and_in_unit_interval():
      v = es._coin("phase", 3)
      assert 0.0 <= v < 1.0
      assert es._coin("phase", 3) == v


  def test_scope_ok_ignores_preexisting_rejects_new(tmp_path):
      repo = _git_repo(tmp_path)
      wp = Path("workspace/transcribe.py")
      (repo / "workspace" / "transcribe.py").write_text("def transcribe(a,s):\n return 'x'\n")
      # repo is ALREADY dirty with an out-of-scope untracked file (like the real
      # operator repo with docs/proposals). This must NOT cause a reject.
      (repo / "preexisting.md").write_text("untracked doc")
      before = es._out_of_scope(repo, wp)
      # candidate edits ONLY workspace + writes under runs/ (in scope)
      (repo / "runs" / "job" / "0000").mkdir(parents=True)
      (repo / "runs" / "job" / "0000" / "x.txt").write_text("ok")
      (repo / "workspace" / "transcribe.py").write_text("def transcribe(a,s):\n return 'y'\n")
      assert es._scope_ok(before, es._out_of_scope(repo, wp)) is True
      # candidate introduces a NEW out-of-scope file -> delta non-empty -> reject
      (repo / "evil.py").write_text("nope")
      assert es._scope_ok(before, es._out_of_scope(repo, wp)) is False


  def test_run_iter_always_appends_and_keeps_if_better(tmp_path, monkeypatch):
      repo = _git_repo(tmp_path)
      job_dir = repo / "runs" / "job"
      # stub candidate edits the workspace + prints a valid YAML block
      stub = repo / "stub.py"
      stub.write_text(
          "import pathlib\n"
          "p = pathlib.Path('workspace/transcribe.py')\n"
          "p.write_text('def transcribe(a, s):\\n    return \\'hi\\'\\n')\n"
          "print('```yaml\\ncapability_investigated: a\\nwhat_i_learned: b\\n"
          "hypothesis: c\\nfingerprint: [t]\\n```')\n"
      )
      # fake verify: monkeypatch run_verify to return a scored result
      from harness import verify as vmod

      class FakeVR:
          def __init__(self, cer):
              self.ok = True
              self.report = {"corpus_cer": cer}
              self.error = None
      monkeypatch.setattr(es, "run_verify", lambda cfg: FakeVR(0.15))

      cfg = es.SimpleConfig(
          job_id="job", repo_root=repo,
          candidate_cmd=f"{sys.executable} {stub}",
          explore=0.0, parent_policy="best", iters=1,
      )
      monkeypatch.setenv("EVOLVE_NO_HARDEN_CLAUDE", "1")
      rec = es.run_iter(cfg, iteration=0, archive=[])
      assert rec.status == "scored"
      loaded = arch.load_archive(job_dir)
      assert len(loaded) == 1                     # ALWAYS appended
      assert arch.read_best(job_dir) == rec.id    # first scored -> best
      # workspace restored to committed stub
      assert "return ''" in (repo / "workspace" / "transcribe.py").read_text()
      # state file written with best_hyp_id
      state = json.loads((job_dir.parent / "_summary" / "job_state.json").read_text())
      assert state["best_hyp_id"] == rec.id
      assert state["best_cer"] == 0.15
  ```
- [ ] Run `python -m pytest tests/test_evolve_simple_iter.py -q` → expect failure.
- [ ] Implement the first part of `harness/evolve_simple.py`:
  ```python
  """harness/evolve_simple.py — the radically simplified self-evolve loop.

  never-prune flat archive + per-iter claude move + verify + keep-if-better.
  No scheduler/lineage/portfolio/cooldown/signature/promotion/policy/gitops.
  """
  from __future__ import annotations

  import hashlib
  import json
  import random
  import subprocess
  from dataclasses import dataclass, field
  from datetime import UTC, datetime
  from pathlib import Path

  from harness import archive as arch
  from harness import candidate_cli as cc
  from harness import config as cfg
  from harness import prompt_simple as ps
  from harness import select_context as sc
  from harness.verify import VerifyConfig, run_verify


  @dataclass
  class SimpleConfig:
      job_id: str
      repo_root: Path = Path(".")
      candidate_cmd: str = "claude -p"
      iters: int = 1
      explore: float = 0.5
      parent_policy: str = "llm"           # llm | random | best
      directive: str = ""
      bans: list[str] = field(default_factory=list)
      pinned: str | None = None
      holdout_every: int = 0
      recent: int = 5
      K: int = 8
      batch: str = "AIG_녹취반출_20250715"
      allowed_path: Path = Path("workspace/transcribe.py")
      baseline_file: Path = Path("baseline/target_cer.json")
      runtime_hard_multiplier: float = cfg.RUNTIME_HARD_MULTIPLIER

      @property
      def job_dir(self) -> Path:
          return self.repo_root / "runs" / self.job_id

      @property
      def summary_dir(self) -> Path:
          return self.repo_root / "runs" / "_summary"


  def _seed_for(job_id: str, iteration: int) -> int:
      h = hashlib.sha256(f"{job_id}:{iteration}".encode()).hexdigest()
      return int(h[:16], 16)


  def _coin(job_id: str, iteration: int) -> float:
      """Deterministic, resume-safe coin in [0, 1) from (job, iter)."""
      return (_seed_for(job_id, iteration) % 1_000_000) / 1_000_000.0


  def _run_git(repo_root: Path, args: list[str], check: bool = True):
      return subprocess.run(["git", *args], cwd=repo_root, check=check,
                            capture_output=True, text=True)


  def _out_of_scope(repo_root: Path, allowed_path: Path) -> set[str]:
      """Set of dirty paths that are NOT workspace/transcribe.py and NOT under
      runs/. (runs/ is gitignored so it won't appear in plain porcelain anyway;
      the prefix check is defensive.)"""
      res = _run_git(repo_root, ["status", "--porcelain", "--untracked-files=all"],
                     check=False)
      allowed = allowed_path.as_posix()
      out: set[str] = set()
      for line in res.stdout.splitlines():
          if not line.strip():
              continue
          path = line[3:]
          if " -> " in path:                       # rename: take the destination
              path = path.split(" -> ", 1)[1]
          if path == allowed or path.startswith("runs/"):
              continue
          out.add(path)
      return out


  def _scope_ok(before: set[str], after: set[str]) -> bool:
      """DELTA scope check: reject only if the candidate introduced NEW
      out-of-scope changes (after - before is non-empty).

      Absolute "tree must be clean except workspace" is WRONG here: the operator
      repo is normally dirty (untracked docs, proposals, plans, .claude.alt/...),
      which would falsely reject EVERY candidate. We snapshot the out-of-scope
      set right before invoking the candidate and compare after — so pre-existing
      untracked files are ignored and only the candidate's own out-of-scope
      writes trip the gate."""
      return not (after - before)


  def _read_json(path: Path) -> dict:
      try:
          return json.loads(path.read_text(encoding="utf-8"))
      except (OSError, json.JSONDecodeError):
          return {}


  def _write_state(cfg_: SimpleConfig, archive: list[arch.ArchiveRecord]) -> None:
      best = arch.best_record(archive)
      state = {
          "job_id": cfg_.job_id,
          "iterations": len(archive),
          "best_hyp_id": best.id if best else None,
          "best_cer": best.cer if best else None,
          "updated_at": datetime.now(UTC).isoformat(),
      }
      cfg_.summary_dir.mkdir(parents=True, exist_ok=True)
      (cfg_.summary_dir / f"{cfg_.job_id}_state.json").write_text(
          json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
      )


  def _write_log(cfg_: SimpleConfig, line: dict) -> None:
      cfg_.summary_dir.mkdir(parents=True, exist_ok=True)
      with (cfg_.summary_dir / f"{cfg_.job_id}_log.jsonl").open("a", encoding="utf-8") as fh:
          fh.write(json.dumps(line, ensure_ascii=False) + "\n")


  def run_iter(
      cfg_: SimpleConfig, iteration: int, archive: list[arch.ArchiveRecord]
  ) -> arch.ArchiveRecord:
      repo = cfg_.repo_root
      cid = arch.next_id(archive)
      out_dir = cfg_.job_dir / cid
      mode = "EXPLORE" if _coin(cfg_.job_id, iteration) < cfg_.explore else "EXPLOIT"
      rng = random.Random(_seed_for(cfg_.job_id, iteration))

      parent, inspr = sc.select_context(
          archive, K=cfg_.K, recent=cfg_.recent, mode=mode,
          policy=cfg_.parent_policy, rng=rng, pinned=cfg_.pinned,
      )

      # Materialize the chosen parent (if any) so the LLM edits real code.
      if parent is not None:
          arch.materialize_parent(cfg_.job_dir, parent.id, repo / cfg_.allowed_path)

      baseline = _read_json(repo / cfg_.baseline_file)
      profile = (repo / "harness" / "prompts" / "candidate_simple.md").read_text(encoding="utf-8")
      frozen_surface = (repo / "frozen" / "asr_backend.py").read_text(encoding="utf-8")
      workspace_body = (repo / cfg_.allowed_path).read_text(encoding="utf-8")
      best = arch.best_record(archive)

      prompt = ps.build_simple_prompt(
          profile=profile, frozen_surface=frozen_surface, workspace_body=workspace_body,
          baseline=baseline, mode=mode, directive=cfg_.directive, bans=cfg_.bans,
          parent=parent, inspirations=inspr, allowed_path=cfg_.allowed_path.as_posix(),
          best_cer=best.cer if best else None, best_hyp_id=best.id if best else None,
          archive=archive,
      )

      # Snapshot out-of-scope dirty set BEFORE the candidate runs, so the
      # delta scope check below ignores pre-existing untracked files.
      before_scope = _out_of_scope(repo, cfg_.allowed_path)

      result = cc.run_candidate_command(
          candidate_cmd=cfg_.candidate_cmd, prompt=prompt, out_dir=out_dir,
          repo_root=repo, workspace_file=cfg_.allowed_path,
      )

      ts = datetime.now(UTC).isoformat()
      meta, reason = cc.parse_candidate_metadata(result.stdout, out_dir=out_dir)

      def _finalize(status: str, cer, score_report, m: dict | None):
          rec = arch.ArchiveRecord(
              id=cid, parents=[parent.id] if parent else [], cer=cer, status=status,
              hypothesis=(m or {}).get("hypothesis", ""),
              what_i_learned=(m or {}).get("what_i_learned", ""),
              fingerprint=(m or {}).get("fingerprint", []),
              score_report=score_report, ts=ts, mode=mode,
              lane=(m or {}).get("lane"),
              capability_investigated=(m or {}).get("capability_investigated", ""),
          )
          arch.append_record(cfg_.job_dir, rec)
          return rec

      # command failed
      if result.returncode != 0:
          rec = _finalize("command_failed", None, None, meta)
          _restore(repo, cfg_.allowed_path)
          _emit_log(cfg_, iteration, mode, parent, rec, kept=False, holdout=None)
          archive.append(rec)
          _write_state(cfg_, archive)
          return rec

      # format reject
      if meta is None:
          rec = _finalize("format_reject", None, None, None)
          _restore(repo, cfg_.allowed_path)
          _emit_log(cfg_, iteration, mode, parent, rec, kept=False, holdout=None)
          archive.append(rec)
          _write_state(cfg_, archive)
          return rec

      # scope reject — candidate introduced a NEW out-of-scope change
      if not _scope_ok(before_scope, _out_of_scope(repo, cfg_.allowed_path)):
          rec = _finalize("scope_reject", None, None, meta)
          _restore(repo, cfg_.allowed_path)
          _emit_log(cfg_, iteration, mode, parent, rec, kept=False, holdout=None)
          archive.append(rec)
          _write_state(cfg_, archive)
          return rec

      # snapshot the evaluated code BEFORE verify/restore so it survives as a parent
      arch.snapshot_candidate(cfg_.job_dir, cid, repo / cfg_.allowed_path)

      vr = run_verify(VerifyConfig(
          repo_root=repo, hyp_id=f"{cfg_.job_id}/{cid}", batch=cfg_.batch,
          workspace_file=cfg_.allowed_path, baseline_file=cfg_.baseline_file,
          runs_dir=Path("runs"), runtime_hard_multiplier=cfg_.runtime_hard_multiplier,
      ))
      if not vr.ok:
          rec = _finalize("rejected", None, None, meta)
          _restore(repo, cfg_.allowed_path)
          _emit_log(cfg_, iteration, mode, parent, rec, kept=False, holdout=None)
          archive.append(rec)
          _write_state(cfg_, archive)
          return rec

      cer = float(vr.report["corpus_cer"])
      rec = _finalize("scored", cer, f"{cid}/score_report.json", meta)
      archive.append(rec)

      # keep-if-better: advance best only on a real improvement
      prior_best = arch.best_record(archive[:-1])
      kept = prior_best is None or cer < prior_best.cer - cfg.KEEP_DELTA_EPS
      if kept:
          arch.write_best(cfg_.job_dir, cid)

      _restore(repo, cfg_.allowed_path)
      _write_state(cfg_, archive)
      _emit_log(cfg_, iteration, mode, parent, rec, kept=kept, holdout=None)
      return rec


  def _restore(repo_root: Path, allowed_path: Path) -> None:
      _run_git(repo_root, ["restore", "--", allowed_path.as_posix()], check=False)


  def _emit_log(cfg_, iteration, mode, parent, rec, kept, holdout):
      delta = ""
      pid = parent.id if parent else "—"
      pcer = f"{parent.cer:.4f}" if parent and parent.cer is not None else "n/a"
      cer_str = f"{rec.cer:.4f}" if rec.cer is not None else rec.status
      decision = "KEEP" if kept else "—"
      hold_str = f" | hold={holdout:.4f}" if holdout is not None else ""
      line = (
          f"iter {iteration:03d} | {mode} | parent={pid}({pcer}) | "
          f"id={rec.id} | cer={cer_str} | {decision}{hold_str}"
      )
      print(line)
      _write_log(cfg_, {
          "iter": iteration, "mode": mode, "parent": pid, "id": rec.id,
          "cer": rec.cer, "status": rec.status, "kept": kept,
          "holdout": holdout, "fingerprint": rec.fingerprint,
      })
  ```
  > Note: `VerifyConfig.hyp_id=f"{cfg_.job_id}/{cid}"` makes verify write under `runs/<job>/<id>/` (matching the archive dir layout). Confirm this matches `arch.snapshot_candidate`'s `runs/<job>/<id>/` location during implementation; if `run_verify`'s `out_dir` differs, set `hyp_id` to the job-relative `f"{cfg_.job_id}/{cid}"` and keep `runs_dir=Path("runs")` so the score_report lands beside the snapshot.
- [ ] Run `python -m pytest tests/test_evolve_simple_iter.py -q` → expect pass.
- [ ] Commit:
  ```
  feat(simple-evolve): run_iter glue (scope check, verify, keep-if-better)

  One iteration end-to-end: deterministic mode -> select_context ->
  materialize parent -> prompt -> candidate -> scope check -> run_verify ->
  ALWAYS append archive row -> advance best.txt only on real improvement.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```

---

## Task 6 — `run_job` loop + `scripts/evolve_simple.py`

The N-iteration driver + the thin CLI exposing the ≤6 knobs.

> **Note (rate-limit backoff, added post-implementation):** `run_iter` wraps the
> candidate call in a backoff retry (`cc.RATE_LIMIT_BACKOFF_MIN` = 5·10·20·40·80·80·80
> min, cumulative ~315 min; ported from `runner.py` + one extra 80 step). On a persistent
> session/token limit it raises `RateLimitAbort`, which `run_job` catches to stop
> cleanly and write `status="aborted_rate_limit"` into the state file (no bogus
> archive row for the aborted iter); rerunning the same `--job-id` resumes. Sleep
> is via the module-level `es._sleep` indirection so tests never actually sleep.

**Files**
- Modify: `harness/evolve_simple.py` (add `run_job`)
- Create: `scripts/evolve_simple.py`
- Test: `tests/test_evolve_simple_job.py`

**Steps**

- [ ] Write failing test `tests/test_evolve_simple_job.py`:
  ```python
  from __future__ import annotations

  import subprocess
  import sys
  from pathlib import Path

  ROOT = Path(__file__).resolve().parents[1]
  if str(ROOT) not in sys.path:
      sys.path.insert(0, str(ROOT))

  from harness import archive as arch
  from harness import evolve_simple as es


  def _git_repo(tmp_path: Path) -> Path:
      subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
      subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
      subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
      ws = tmp_path / "workspace"
      ws.mkdir()
      (ws / "transcribe.py").write_text("def transcribe(a, s):\n    return ''\n")
      subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
      subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
      return tmp_path


  def test_run_job_produces_exactly_n_rows(tmp_path, monkeypatch):
      repo = _git_repo(tmp_path)
      stub = repo / "stub.py"
      stub.write_text(
          "import pathlib\n"
          "p = pathlib.Path('workspace/transcribe.py')\n"
          "p.write_text('def transcribe(a, s):\\n    return \\'hi\\'\\n')\n"
          "print('```yaml\\ncapability_investigated: a\\nwhat_i_learned: b\\n"
          "hypothesis: c\\nfingerprint: [t]\\n```')\n"
      )
      cers = iter([0.20, 0.18, 0.19])

      class FakeVR:
          def __init__(self, cer):
              self.ok, self.report, self.error = True, {"corpus_cer": cer}, None
      monkeypatch.setattr(es, "run_verify", lambda cfg: FakeVR(next(cers)))
      monkeypatch.setenv("EVOLVE_NO_HARDEN_CLAUDE", "1")

      cfg = es.SimpleConfig(job_id="job", repo_root=repo,
                            candidate_cmd=f"{sys.executable} {stub}",
                            iters=3, explore=0.0, parent_policy="best")
      best_id = es.run_job(cfg)
      loaded = arch.load_archive(repo / "runs" / "job")
      assert len(loaded) == 3                  # exactly N, no hidden multiplier
      assert best_id == "0001"                 # 0.18 is the min


  def test_cli_parses_six_knobs(monkeypatch):
      import scripts.evolve_simple as cli
      ns = cli.parse_args([
          "--job-id", "j", "--iters", "5", "--directive", "try vad",
          "--explore", "0.3", "--ban", "beam sweep", "--pin", "0002",
          "--holdout-every", "2", "--parent-policy", "random",
      ])
      assert ns.iters == 5
      assert ns.explore == 0.3
      assert ns.parent_policy == "random"
      assert ns.ban == ["beam sweep"]
      assert ns.pin == "0002"
      assert ns.holdout_every == 2
  ```
- [ ] Run `python -m pytest tests/test_evolve_simple_job.py -q` → expect failure.
- [ ] Append `run_job` to `harness/evolve_simple.py`:
  ```python
  def run_job(cfg_: SimpleConfig) -> str | None:
      """Run exactly cfg_.iters iterations. Returns the best id (or None)."""
      cc.check_bypass_in_production(cfg_.iters, commit_results=False)
      archive = arch.load_archive(cfg_.job_dir)
      start = len(archive)
      for i in range(cfg_.iters):
          run_iter(cfg_, iteration=start + i, archive=archive)
          if cfg_.holdout_every and arch.read_best(cfg_.job_dir):
              # holdout cadence handled by run_job (Task 8 wires the actual call)
              _maybe_holdout(cfg_, iteration=start + i, archive=archive)
      best = arch.best_record(archive)
      return best.id if best else None


  def _maybe_holdout(cfg_: SimpleConfig, iteration: int, archive) -> None:
      """Placeholder hook — Task 8 implements the holdout invocation cadence."""
      return None
  ```
- [ ] Implement `scripts/evolve_simple.py`:
  ```python
  #!/usr/bin/env python3
  """scripts/evolve_simple.py — thin CLI for the simple self-evolve loop."""
  from __future__ import annotations

  import argparse
  import sys
  from pathlib import Path

  ROOT = Path(__file__).resolve().parents[1]
  if str(ROOT) not in sys.path:
      sys.path.insert(0, str(ROOT))

  from harness.evolve_simple import SimpleConfig, run_job  # noqa: E402


  def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
      p = argparse.ArgumentParser(description="Simple self-evolve loop")
      p.add_argument("--job-id", required=True)
      p.add_argument("--iters", type=int, default=1)
      p.add_argument("--directive", default="")
      p.add_argument("--explore", type=float, default=0.5)
      p.add_argument("--ban", action="append", default=[])
      p.add_argument("--pin", default=None)
      p.add_argument("--holdout-every", type=int, default=0)
      p.add_argument("--parent-policy", choices=["llm", "random", "best"], default="llm")
      p.add_argument("--candidate-cmd", default="claude -p")
      p.add_argument("--repo-root", type=Path, default=Path("."))
      return p.parse_args(argv)


  def main(argv: list[str] | None = None) -> int:
      ns = parse_args(argv)
      cfg = SimpleConfig(
          job_id=ns.job_id, repo_root=ns.repo_root, candidate_cmd=ns.candidate_cmd,
          iters=ns.iters, explore=ns.explore, parent_policy=ns.parent_policy,
          directive=ns.directive, bans=ns.ban, pinned=ns.pin,
          holdout_every=ns.holdout_every,
      )
      best = run_job(cfg)
      print(f"job {ns.job_id} done — best={best}")
      return 0


  if __name__ == "__main__":
      raise SystemExit(main())
  ```
- [ ] Run `python -m pytest tests/test_evolve_simple_job.py -q` → expect pass.
- [ ] Commit:
  ```
  feat(simple-evolve): run_job loop + scripts/evolve_simple.py CLI

  Exactly --iters candidates (no hidden multiplier). Six operator knobs:
  --iters/--directive/--explore/--ban/--pin/--holdout-every/--parent-policy.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```

---

## Task 7 — `scripts/archive_summary.py` leaderboard

Read `archive.jsonl` (+ holdout sidecars) and print in-loop CER and holdout CER side by side.

**Files**
- Create: `scripts/archive_summary.py`
- Test: `tests/test_archive_summary.py`

**Steps**

- [ ] Write failing test `tests/test_archive_summary.py`:
  ```python
  from __future__ import annotations

  import sys
  from pathlib import Path

  ROOT = Path(__file__).resolve().parents[1]
  if str(ROOT) not in sys.path:
      sys.path.insert(0, str(ROOT))

  from harness import archive as arch
  import scripts.archive_summary as summ


  def test_leaderboard_sorts_by_cer_and_marks_best(tmp_path):
      job_dir = tmp_path / "runs" / "job"
      for cid, cer, st in [("0000", 0.20, "scored"), ("0001", 0.15, "scored"),
                           ("0002", None, "rejected"), ("0003", 0.18, "scored")]:
          arch.append_record(job_dir, arch.ArchiveRecord(
              id=cid, parents=[], cer=cer, status=st, hypothesis="h",
              what_i_learned="l", fingerprint=["t"], score_report=None, ts="t"))
      arch.write_best(job_dir, "0001")
      text = summ.render_leaderboard(job_dir, holdout={})
      lines = text.splitlines()
      # best row carries a marker and appears before worse scored rows
      assert "0001" in text and "*" in text
      assert lines.index([l for l in lines if "0001" in l][0]) < \
             lines.index([l for l in lines if "0003" in l][0])


  def test_holdout_column_rendered_when_present(tmp_path):
      job_dir = tmp_path / "runs" / "job"
      arch.append_record(job_dir, arch.ArchiveRecord(
          id="0000", parents=[], cer=0.15, status="scored", hypothesis="h",
          what_i_learned="l", fingerprint=["t"], score_report=None, ts="t"))
      text = summ.render_leaderboard(job_dir, holdout={"0000": 0.16})
      assert "0.1600" in text
  ```
- [ ] Run `python -m pytest tests/test_archive_summary.py -q` → expect failure.
- [ ] Implement `scripts/archive_summary.py`:
  ```python
  #!/usr/bin/env python3
  """scripts/archive_summary.py — flat-archive leaderboard (in-loop + holdout)."""
  from __future__ import annotations

  import argparse
  import json
  import sys
  from pathlib import Path

  ROOT = Path(__file__).resolve().parents[1]
  if str(ROOT) not in sys.path:
      sys.path.insert(0, str(ROOT))

  from harness import archive as arch  # noqa: E402


  def _load_holdout(summary_dir: Path, job_id: str) -> dict[str, float]:
      """Map hyp_id -> holdout cer from any <job>_holdout.jsonl sidecar (Task 8)."""
      path = summary_dir / f"{job_id}_holdout.jsonl"
      out: dict[str, float] = {}
      if path.is_file():
          for line in path.read_text(encoding="utf-8").splitlines():
              if not line.strip():
                  continue
              try:
                  rec = json.loads(line)
              except json.JSONDecodeError:
                  continue
              if rec.get("hyp_id") and rec.get("holdout_cer") is not None:
                  out[rec["hyp_id"]] = float(rec["holdout_cer"])
      return out


  def render_leaderboard(job_dir: Path, holdout: dict[str, float]) -> str:
      archive = arch.load_archive(job_dir)
      best_id = arch.read_best(job_dir)
      scored = sorted(
          [r for r in archive if r.status == "scored" and r.cer is not None],
          key=lambda r: r.cer,
      )
      lines = [
          f"# Leaderboard — {job_dir.name}  ({len(archive)} candidates, "
          f"{len(scored)} scored)",
          "| rank | id | cer | holdout | fingerprint | hypothesis |",
          "|------|----|-----|---------|-------------|------------|",
      ]
      for i, r in enumerate(scored, 1):
          mark = " *" if r.id == best_id else ""
          hold = holdout.get(r.id)
          hold_str = f"{hold:.4f}" if hold is not None else "—"
          fp = ",".join(r.fingerprint)
          hyp = (r.hypothesis or "").replace("\n", " ")[:60]
          lines.append(f"| {i} | {r.id}{mark} | {r.cer:.4f} | {hold_str} | {fp} | {hyp} |")
      non_scored = [r for r in archive if r.status != "scored"]
      if non_scored:
          lines.append("")
          lines.append(f"({len(non_scored)} non-scored: "
                       + ", ".join(sorted({r.status for r in non_scored})) + ")")
      return "\n".join(lines)


  def main(argv: list[str] | None = None) -> int:
      p = argparse.ArgumentParser(description="Flat-archive leaderboard")
      p.add_argument("--job", required=True)
      p.add_argument("--repo-root", type=Path, default=Path("."))
      ns = p.parse_args(argv)
      job_dir = ns.repo_root / "runs" / ns.job
      summary_dir = ns.repo_root / "runs" / "_summary"
      print(render_leaderboard(job_dir, _load_holdout(summary_dir, ns.job)))
      return 0


  if __name__ == "__main__":
      raise SystemExit(main())
  ```
- [ ] Run `python -m pytest tests/test_archive_summary.py -q` → expect pass.
- [ ] Commit:
  ```
  feat(simple-evolve): archive_summary leaderboard (in-loop + holdout)

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```

---

## Task 8 — Holdout state-anchor wiring (`--holdout-every`)

> **SUPERSEDED — holdout is now operator-manual; auto `--holdout-every` removed (re-seal unreliable as candidate user).**

The new loop already writes `runs/_summary/<job>_state.json::best_hyp_id`/`best_cer` (Task 5) so `scripts/evaluate_holdout.py::_best_eval_run` can anchor. This task implements the `--holdout-every` cadence: after a new best on every Kth new-best, run the existing holdout script and record `hyp_id -> holdout_cer` into a `<job>_holdout.jsonl` sidecar (read by the leaderboard in Task 7).

**Files**
- Modify: `harness/evolve_simple.py` (`_maybe_holdout` real impl + per-iter log holdout column)
- Test: `tests/test_evolve_simple_iter.py` (add a holdout-cadence test)

**Steps**

- [ ] Add failing test to `tests/test_evolve_simple_iter.py`:
  ```python
  def test_maybe_holdout_records_sidecar_on_cadence(tmp_path, monkeypatch):
      from harness import evolve_simple as es
      from harness import archive as arch
      job_dir = tmp_path / "runs" / "job"
      arch.append_record(job_dir, arch.ArchiveRecord(
          id="0000", parents=[], cer=0.15, status="scored", hypothesis="h",
          what_i_learned="l", fingerprint=["t"], score_report=None, ts="t"))
      arch.write_best(job_dir, "0000")
      cfg = es.SimpleConfig(job_id="job", repo_root=tmp_path, holdout_every=1)
      # fake the holdout subprocess: return a fixed cer for the current best
      monkeypatch.setattr(es, "_invoke_holdout", lambda cfg_, best_id: 0.16)
      archive = arch.load_archive(job_dir)
      es._maybe_holdout(cfg, iteration=0, archive=archive)
      sidecar = (tmp_path / "runs" / "_summary" / "job_holdout.jsonl")
      assert sidecar.is_file()
      rows = [__import__("json").loads(l) for l in sidecar.read_text().splitlines() if l.strip()]
      assert rows[-1]["hyp_id"] == "0000"
      assert rows[-1]["holdout_cer"] == 0.16
  ```
- [ ] Run `python -m pytest tests/test_evolve_simple_iter.py -q -k maybe_holdout` → expect failure.
- [ ] Replace the `_maybe_holdout` placeholder + add `_invoke_holdout` in `harness/evolve_simple.py`:
  ```python
  import subprocess as _sp


  def _invoke_holdout(cfg_: SimpleConfig, best_id: str) -> float | None:
      """Run the existing holdout script anchored on this job's state and return
      the holdout corpus_cer. Requires the JOB_DONE.lock + --unseal contract of
      scripts/evaluate_holdout.py; we touch the lock for the duration. Best-effort:
      a failed holdout returns None and never crashes the loop."""
      lock = cfg_.summary_dir / "JOB_DONE.lock"
      created = False
      if not lock.is_file():
          lock.parent.mkdir(parents=True, exist_ok=True)
          lock.write_text("simple-evolve holdout cadence\n", encoding="utf-8")
          created = True
      try:
          _sp.run(
              [__import__("sys").executable, "-m", "scripts.evaluate_holdout",
               "--unseal", "--job-id", cfg_.job_id,
               "--summary-dir", str(cfg_.summary_dir),
               "--runs-dir", str(cfg_.repo_root / "runs")],
              cwd=cfg_.repo_root, check=False,
          )
      finally:
          if created and lock.is_file():
              lock.unlink()
      # the holdout script writes docs/reports/<job>_HOLDOUT_<date>.json with
      # holdout_cer; read the newest one for this job.
      reports = sorted((cfg_.repo_root / "docs" / "reports").glob(f"{cfg_.job_id}_HOLDOUT_*.json"))
      if not reports:
          return None
      data = _read_json(reports[-1])
      return data.get("holdout_cer")


  def _maybe_holdout(cfg_: SimpleConfig, iteration: int, archive) -> None:
      best = arch.best_record(archive)
      if best is None:
          return
      sidecar = cfg_.summary_dir / f"{cfg_.job_id}_holdout.jsonl"
      already = set()
      if sidecar.is_file():
          for line in sidecar.read_text(encoding="utf-8").splitlines():
              if line.strip():
                  try:
                      already.add(json.loads(line).get("hyp_id"))
                  except json.JSONDecodeError:
                      pass
      if best.id in already:
          return  # only evaluate each new best once
      holdout_cer = _invoke_holdout(cfg_, best.id)
      cfg_.summary_dir.mkdir(parents=True, exist_ok=True)
      with sidecar.open("a", encoding="utf-8") as fh:
          fh.write(json.dumps({
              "iter": iteration, "hyp_id": best.id,
              "in_loop_cer": best.cer, "holdout_cer": holdout_cer,
          }, ensure_ascii=False) + "\n")
  ```
  > Implementation note: the cadence is "each new best, capped to once per id"; the `--holdout-every K` knob throttles by only invoking when `(count of recorded holdouts) % K == 0` — wire that in `run_job` by counting sidecar rows before calling `_maybe_holdout`, or simplest: call `_maybe_holdout` only every Kth iteration in `run_job`. Keep the one-per-id dedup regardless.
- [ ] Run `python -m pytest tests/test_evolve_simple_iter.py -q -k maybe_holdout` → expect pass.
- [ ] Run the full simple-evolve suite: `python -m pytest tests/test_candidate_cli.py tests/test_simple_archive.py tests/test_select_context.py tests/test_prompt_simple.py tests/test_evolve_simple_iter.py tests/test_evolve_simple_job.py tests/test_archive_summary.py -q` → expect all pass.
- [ ] Commit:
  ```
  feat(simple-evolve): holdout cadence anchored on <job>_state.json best_hyp_id

  --holdout-every wires scripts/evaluate_holdout.py against the new loop's
  state file; results recorded to <job>_holdout.jsonl for the leaderboard.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```

---

## Task 9 — [POST-MVP] `base_systems/` ensemble enablement

> **POST-MVP — do NOT build during the MVP.** Designed here so the contract is forward-compatible; implement only after Phase-2 shows single-system search working and an ensemble path is wanted. (Master design §5; diversity strategy §3.)

Make ROVER/MBR ensembling physically possible without breaking the single-file contract.

**Design (build later):**
- **Snapshot path:** when an iter is kept (or is a per-cell elite), copy `workspace/transcribe.py` → `base_systems/<job>_<id>.py` (append-only, never pruned — same philosophy as the archive). ~10 lines added to the keep branch of `run_iter`.
- **Frozen helper:** add `frozen/base_systems.py` exposing `list_base_systems() -> list[str]` and `transcribe_with(hyp_id: str, audio, sr) -> str`. `transcribe_with` imports `base_systems/<hyp_id>.py` and calls its `transcribe(audio, sr)`; each base system still reaches the model only through the frozen `asr_backend`, so the model stays frozen and the `verify.check_workspace_static` backend guard is unaffected.
- **Read surface:** add `base_systems/` to the candidate's allowed-read sandbox (settings.json deny list keeps it non-writable — candidate may still only write `workspace/transcribe.py`); the scope check in `_scope_ok` already rejects writes outside `workspace/transcribe.py` + `runs/`, so it covers this for free.
- **Prompt:** the inspirations block lists available `base_systems` ids with their dominant-axis descriptor so the LLM picks complementary systems (one coverage-strong + one substitution-strong).
- **Cost honesty:** an ensemble runs K decodes/file → K× runtime; the existing `guards.check_runtime` (cap = baseline × `RUNTIME_HARD_MULTIPLIER`=7.0) rejects ensembles that blow the budget. No new mechanism.
- **Tests (later):** `transcribe_with` round-trips an archived system; a candidate that imports two base systems and votes passes the static backend guard; runtime-cap rejects a too-large ensemble.

This task ships **no code** in the MVP — it is a placeholder so the schema (`base_systems/` dir, frozen helper signature) is reserved.

---

## Phase-2 operator runbook (NOT a code task — a checklist)

Run after Tasks 1–8 land. Goal: decide whether to cut over from the old controller, and whether the LLM's *move selection* (not just the simplification) earns its keep. Same stub, same `--iters`, same seed across all jobs; compare **best holdout CER** and **iters-to-best**.

- [ ] Confirm a clean stub `workspace/transcribe.py` is committed (the from-scratch starting point) and `baseline/target_cer.json` + `baseline/noise_floor.json` are present.
- [ ] Confirm holdout (0813) is sealed (`chmod 000`) and `scripts/evaluate_holdout.py --dry-run` reports glue OK.
- [ ] **Job A — old controller (baseline):** run the legacy `scripts/evolve.py` for N iters; record final best 0715 CER + holdout CER (via `evaluate_holdout`).
- [ ] **Job B — new, `--parent-policy llm`:** `python -m scripts.evolve_simple --job-id sx_llm --iters N --explore 0.5 --parent-policy llm --candidate-cmd "claude -p" --holdout-every 5`.
- [ ] **Job C — new, `--parent-policy random`:** identical knobs, `--parent-policy random` (LLM edits, seeded RNG picks the parent — control for "does LLM move-choice matter").
- [ ] **Job D — new, `--parent-policy best`:** identical knobs, `--parent-policy best` (pure hill-climb control).
- [ ] For each job run `python -m scripts.archive_summary --job <id>` and capture the in-loop + holdout leaderboard.
- [ ] **Decision gate (cutover):** cut over to the new loop **iff** B beats A on holdout best CER **AND** B beats C and D on holdout. If B beats A but not C/D → report honestly: "simplification succeeded; LLM move-selection unproven" (still a simplicity win — keep the new loop with `--parent-policy random` or `best` as default).
- [ ] Sanity: confirm each new-loop job produced **exactly N** archive rows (no hidden `iters*3+10` multiplier) and that `archive.jsonl` has every candidate (scored + rejected) with `parents` for full lineage reconstruction.
- [ ] If cutover approved: schedule the (separate, out-of-scope) work to rename `candidate_simple.md`→`candidate.md`, point `scripts/evolve.py` at the new loop (preserve old as `runner_legacy` one cycle), retire the old controller modules, and update `AGENTS.md`/`docs/SSOT.md`.

---

## Self-Review

**Spec coverage (cross-check against master-design locked decisions):**
- [ ] Flat never-pruned `runs/<job>/archive.jsonl` (1 row/candidate) + per-candidate dir (`transcribe.py`, `candidate.diff`, `score_report.json`, `prompt.md`, `claude_stdout.txt`) + `best.txt` + `runs/_summary/<job>_state.json` with `best_hyp_id`+`best_cer` — Tasks 2, 5. Confirm nothing is ever deleted/rolled-back from the archive (only `git restore workspace/transcribe.py`).
- [ ] New modules only; no old controller module modified — verify with `git diff --stat` touches only the Created files + new tests.
- [ ] Per-iter loop matches §3: deterministic seeded coin → select_context → materialize parent → build prompt → run_candidate_command → scope check → run_verify → ALWAYS append → keep-if-better → restore — Task 5.
- [ ] `parent_policy {llm,random,best}` all implemented; `llm`=weighted_random (Boltzmann / (1+children)), EXPLOIT anchors on best — Task 3.
- [ ] context = parent + top-3 + 2-diverse (dedup by fingerprint) + recent N + learnings ledger (last 40, dedup) — Tasks 3, 4.
- [ ] knobs ≤6 (`--iters`,`--directive`,`--explore`,`--ban`/`--pin`,`--holdout-every`,`--parent-policy`); `RUNTIME_HARD_MULTIPLIER`+`KEEP_DELTA_EPS` reused, not knobs — Task 6, constants from `harness/config.py`.
- [ ] one log line/iter to stdout + `runs/_summary/<job>_log.jsonl` — Task 5 `_emit_log`.
- [ ] Ensemble explicitly deferred + designed — Task 9.
- [ ] Tests under base python (`python -m pytest`); holdout via existing `scripts/evaluate_holdout.py` on new-best — Task 8 + runbook.

**Placeholder scan:** every step contains real test code + real implementation code; no "TBD"/"add error handling"/"similar to above". The only deliberate placeholders are the Task-6 `_maybe_holdout` stub (replaced with real code in Task 8) and Task-9 (explicitly POST-MVP, ships no code) — both flagged.

**Type consistency:** `ArchiveRecord.cer: float | None` (None for non-scored rows); `best_record`/`_scored` filter `status=="scored" and cer is not None`. `select_context` returns `(ArchiveRecord | None, list[ArchiveRecord])`. `VerifyResult.report["corpus_cer"]` is read only when `vr.ok`. `_coin` returns float in `[0,1)`; `explore` compared with `<`. `run_verify` is imported into `evolve_simple` module namespace so tests can monkeypatch `es.run_verify`.

**Open implementation checks (resolve while coding, not blockers):**
- [ ] Confirm `run_verify`'s output dir for `hyp_id=f"{job}/{id}"` lands at `runs/<job>/<id>/score_report.json` (beside the snapshot). Adjust `VerifyConfig.hyp_id`/`runs_dir` if not.
- [ ] Confirm `scripts/evaluate_holdout.py` writes `holdout_cer` into its `<job>_HOLDOUT_<date>.json` sidecar (it does — `_write_holdout_report` writes `"holdout_cer"`); `_invoke_holdout` reads that key.
- [ ] Confirm `pyyaml` import name (`import yaml`) resolves under base python (it is already a dep used by `runner.py`).
