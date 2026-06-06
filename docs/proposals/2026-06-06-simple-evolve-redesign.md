# 제안 — self-evolve harness 백지 재설계 (flat archive · LLM 주도 · 단순/제어가능)

> 2026-06-06. 작성 근거: 4개 독립 설계 에이전트(최소루프 / 다양성·수렴 / 검증사례 / 운영·측정·이행) + 코드 grounding.
> 동기: 현 harness(6모드 scheduler + bounded-set lineage 상태기계 + near_best 컷 + cooldown + family 라벨링 + gated promotion)는 **과복잡 → 운영자가 예측·제어 불가**, phase3_030(200iter)에서 0.163 정체(역대 0.154 미달). 역대 0.154는 *더 단순한* 옛 버전에서 나옴 → 복잡도가 결과를 사주지 못했다.
> 목표: **운영자가 한 문장으로 이해하는 루프** + **다양한 알고리즘 탐색을 통해 best 도달**.

---

## 0. 한 문장 요약

> **never-prune flat archive + 매 iter LLM이 archive를 보고 다음 수를 정함 + verify 채점 + keep-if-better.** 그게 전부다. scheduler·lineage·portfolio·cooldown·signature·promotion 전부 삭제.

이 한 줄이 곧 운영자의 멘탈 모델 전체다. "왜 그렇게 했나?"는 항상 둘 중 하나 — (a) 기계적 keep-if-better, (b) LLM이 로그에 남긴 rationale.

---

## 1. 왜 이게 옳은가 (검증된 사례가 말해주는 것)

AlphaEvolve / FunSearch / Darwin-Gödel Machine(DGM) / OpenEvolve — **4개 모두 동일한 5-박스 루프**다:

```
init: archive ← { stub }
loop N회:
  parent, inspirations ← SAMPLE(archive)      # 1개 변형 대상 + few-shot 예시 몇 개
  child ← LLM_MUTATE(parent, inspirations)     # claude -p 가 파일 편집
  score, artifacts ← EVALUATE(child)           # 점수 + 에러/실패 케이스
  INSERT(archive, child, score)                # 저장 (worse여도)
return best(archive)
```

**그리고 4개 모두 우리가 가진 복잡 기계장치(mode scheduler / cooldown / family 라벨링 / gated promotion)를 *의도적으로 안 가진다*.** 이유:
- **explore vs exploit 은 schedule 이 아니라 stochastic parent 선택의 emergent 속성.** Boltzmann 온도 + "자식 많은 부모 패널티"가 곧 explore/exploit 다이얼이다. scheduler 는 이걸 손으로 근사한 것이고, 데이터와 싸운다(우리 메모: diversity_stall 과발동).
- **gated promotion(champion+rollback)은 greedy hill-climbing** = stepping-stone 을 가지치기 → 우리가 본 바로 그 증상("explore reject→champion rollback→못 키움", C2). 검증된 시스템은 *worse여도 archive에 남기고*, best는 따로 *보고*만 한다.
- **cooldown / family 라벨링**: 결정적 sampler가 같은 부모만 재선택해서 생긴 문제의 패치. stochastic + 자식 패널티면 자동 해소.

**다양성·탐색은 외부 controller가 아니라 archive + sampler의 속성이다.** ([[harness-single-head-c2]], [[phase3_030-diversity-cultivation]] 의 정확한 처방.)

출처: AlphaEvolve arxiv.org/abs/2506.13131 · FunSearch (Nature, PMC10794145) · DGM arxiv.org/abs/2505.22954 · OpenEvolve github.com/algorithmicsuperintelligence/openevolve.

---

## 2. 데이터 모델 — archive 1개 jsonl + 후보당 dir. 이게 상태의 전부.

```
runs/<job>/
  archive.jsonl          # append-only, 후보 1개당 1줄. 절대 prune 안 함.
  best.txt               # best 후보 id (캐시; archive에서 min(cer)로 재생성 가능)
  <id>/
    transcribe.py        # 평가된 그대로의 전체 파일 (자기완결 parent 재료)
    candidate.diff       # parent 대비 diff (사람이 읽음)
    score_report.json    # judge/evaluate.py 출력 (corpus_cer + 축 + per-file)
    prompt.md            # 보낸 프롬프트 그대로 (재현성)
    claude_stdout.txt
```

`archive.jsonl` 한 줄:
```json
{"id":"0042","parents":["0031"],"cer":0.1631,"status":"scored",
 "hypothesis":"...LLM이 시도한 수와 이유...","what_i_learned":"...표면 사실(부정 포함)...",
 "fingerprint":["vad","merge"],"score_report":"0042/score_report.json","ts":"..."}
```

**archive 밖 상태 = 사실상 없음.** mode 카운터·cooldown 표·lineage/portfolio 객체·promotion map 전부 없음. `best.txt`는 1줄 캐시. 운영자는 `cat archive.jsonl | jq` 하나로 전부 파악. **worse 후보도 영원히 부모 후보로 남는다 → C2 "유망한데 나쁜 거 육성" 기계장치(lineage set·micro_bank·near_best 컷·dead-end factor) 전부 불필요.**

---

## 3. iteration (실제 의사코드 ~20줄)

```python
def run_job(job, n_iters, K=8, recent=5, explore=0.5, parent_policy="llm"):
    archive = load_or_init(job)                  # archive.jsonl 읽기 (비어도 됨)
    for i in range(n_iters):
        cid  = next_id(archive)
        mode = "EXPLORE" if coin(seed=(job,i)) < explore else "EXPLOIT"   # 결정적, RNG resume-safe
        parent, inspr = select_context(archive, K, recent, mode, parent_policy)  # §4
        write_workspace(parent.code)             # LLM이 진짜 parent를 편집하도록 복원
        prompt = build_prompt(candidate_md, backend_surface, baseline,
                              mode, directive, bans, parent, inspr)
        res  = run_candidate_command("claude -p", prompt, out=dir(cid))   # 하드닝 재사용
        meta = parse_yaml_block(res.stdout)      # hypothesis / what_i_learned / fingerprint
        if scope_dirty_beyond_transcribe(): reject(); continue          # §6 단순 scope 체크
        vr   = run_verify(cid)                    # judge/evaluate + guards 재사용
        append(archive, record(cid, meta, vr))    # 항상 append (worse·broken·better 무관)
        if vr.ok and (best is None or vr.cer < best.cer - KEEP_EPS):
            write_best(cid)                        # 개선 시에만 best 전진
        git_restore("workspace/transcribe.py")     # 다음 iter 위해 표면 리셋
    return best(archive)
```

scheduler 없음, promotion CAS 없음, cooldown 없음. "keep-if-better"는 `if` 하나. 똑똑한 일은 전부 `select_context`(단순) + LLM 안에서 일어난다.

---

## 4. parent / context 선택 (scheduler가 아니라 sampling)

검증된 시스템들의 교집합 정책 (FunSearch k=2 + OpenEvolve 3-top/2-diverse + DGM 자식패널티):

```python
def select_context(archive, K, recent, mode, policy):
    # parent: 변형 대상 1개 — 결정적 argmax 금지(=premature convergence). 확률적.
    if policy == "best":   parent = best(archive)                     # 대조군: 순수 hill-climb
    elif policy == "random": parent = seeded_choice(archive)          # 대조군: 무작위 부모
    else:  # "llm" (treatment) — 또는 weighted_random
        w = lambda p: exp(score_norm(p)/T) / (1 + children(p))        # 성능↑ · 자식 많으면↓
        parent = best(archive) if mode=="EXPLOIT" else weighted_random(archive, w)
    # inspirations: few-shot 메뉴 (LLM이 조합/탐색 재료로 봄)
    inspr = top_by_cer(archive, 3) + diverse_sample(archive, 2) + recent_attempts(archive, recent)
    return parent, dedup_by_fingerprint(inspr)
```

- **항상 best만 보여주지 않는다** → mono-culture 방지, combine 가능. (best + 다양한 high-performer + 최근 실패 + learnings ledger)
- **자식 많은 부모 패널티(DGM)** = 작은 예산에서 stepping-stone 재방문을 강제하는 단 하나의 항.
- knob: `K`, `recent` (그리고 온도 `T`는 내부 상수로 시작).

### 다양성 보존 (선택: MAP-Elites를 *저장*이 아니라 *조회*로)
flat archive 위에 **읽을 때만** 축으로 cell을 묶어 cell당 best를 메뉴에 노출(저장은 여전히 flat, 절대 prune 안 함). descriptor는 score_report에 *이미 있는* 값으로: dominant error axis(del/sub/hallucination) × approach tag(fingerprint 거칠게). 150 iter 예산엔 full MAP-Elites grid는 과함 → **flat + diversity-aware sampling으로 시작, island/grid는 mono-culture 관측되면 그때.**

---

## 5. 앙상블(ROVER/MBR) 가능하게 — 유일하게 필요한 contract 변경

0.154급은 보통 *서로 다른 강한 시스템 ≥2개*를 투표(ROVER/MBR)해서 나온다. 현재 단일파일 contract는 reject된 접근의 코드가 rollback돼서 앙상블 불가. 최소 수정:
- keep/elite 시 `workspace/transcribe.py`를 **읽기전용 append-only** `base_systems/<id>.py`로 스냅샷.
- frozen helper `frozen.base_systems.transcribe_with(id, audio, sr)` 추가 → 후보가 과거 강한 시스템들을 불러 앙상블 가능. 후보는 여전히 단일파일 쓰기, 모델은 여전히 frozen, runtime hard-cap(×7)이 K개 앙상블을 정직하게 유지.

**이건 결정 사항** — 앙상블을 1급으로 지원할지(=이 스냅샷 추가) vs 일단 단일 시스템 탐색만 보고 나중에 추가할지.

---

## 6. 삭제 vs 재사용

| 현재 모듈 | 운명 | 이유 |
|---|---|---|
| `scheduler.py` (6모드) | **삭제** | LLM이 메뉴에서 수를 고름 |
| `lineage.py` (set 상태기계) | **삭제** | flat archive가 "seed 육성" 대체 |
| `portfolio.py` | **삭제** | archive가 곧 portfolio, 선택은 `select_context` |
| `cooldown.py` | **삭제** | stochastic+자식패널티로 자동 해소 |
| `signature.py` (family 라벨) | **삭제** | LLM 자유 `fingerprint`(dedup 힌트만) |
| `promotion.py` (flock+CAS) | **삭제** | 단일 운영자·단일 잡, champion 경합 없음. `best.txt` 1줄로 대체 |
| `policy.py` | **삭제/흡수** | 남는 정책은 `KEEP_EPS` 상수 하나 |
| `gitops.py` (worktree/CAS) | **단순화** | `git diff` 캡처 + `git restore` 만 |
| `runner.py` (2772줄 결정경로) | **~200줄로 교체** | = §3 루프 |
| **재사용 (그대로)** | | |
| `judge/evaluate.py` (`evaluate_batch`) | **재사용** | 순수 함수. 점수=metric, 손대지 말 것 |
| `harness/verify.py` (`run_verify`) + `guards.py` | **재사용** | 순수. 정적 scope + judge + numeric guard |
| `run_candidate_command`+`_harden_candidate_cmd`+`parse_candidate_metadata` | **lift → `harness/candidate_cli.py`** | 하드닝(`--disallowedTools=Bash,...`)·diff캡처·YAML파싱. runner 전체를 import하지 않도록 작은 모듈로 분리 |
| `harness/prompts/candidate.md` | **재사용(소폭 편집)** | mode/family/parent-diff 문단만 제거, YAML 출력 contract 유지 |
| `.claude/settings.json` 샌드박스 + hooks | **그대로** | controller 무관 — "transcribe.py만 편집"을 실재화 |
| `scripts/evaluate_holdout.py` | **그대로** | §7 |

**scope guard 단순화**: 새 루프는 rollback이 없으므로 snapshot/poison 기계장치 ~200줄 불필요. 후보 실행 후 `git status --porcelain`이 `workspace/transcribe.py`(+`runs/<id>/`)만 dirty인지 한 줄 체크 → 아니면 reject. champion 보호가 없으니 나쁜 iter는 그냥 낮은 점수 archive 행일 뿐, `git restore`가 유일한 정리.

---

## 7. 측정 타당성 (핵심 — 프로젝트의 존재 이유)

> **갱신**: holdout 자동 호출(`--holdout-every`)은 제거됨 — 잡 종료 후 운영자가 `python scripts/evaluate_holdout.py --unseal --job-id <id>` 로 **수동** 실행한다 (candidate user 로 실행 시 자동 재봉인 `chmod -R 000` 가 불안정하기 때문).

LLM이 이제 *수까지* 고르므로 confound 위험. 두 가지 방어:

**A. 11파일 in-loop 과적합** → **holdout 규율.** `scripts/evaluate_holdout.py`(unseal→eval→re-seal in `finally`, lock-gated) 그대로 재사용. **new-best 때마다**(또는 `--holdout-every K`) 실행, **in-loop CER와 holdout CER 항상 같이 보고.** holdout이 ≥2σ 퇴보하면 `OVERFIT` 플래그 — 결과로 안 침. (단 결합: 새 루프가 `<job>_state.json`에 `best_hyp_id` 한 필드를 써야 holdout 앵커가 잡힘.)

**B. LLM의 수 선택이 실제로 기여하나?** → **`--parent-policy {llm,random,best}` 빌트인 ablation.**
- `llm`: archive 리더보드 보고 LLM이 부모 선택(treatment)
- `random`: 고정시드 RNG가 부모 선택, LLM은 편집만(대조)
- `best`: 항상 best=부모, 순수 hill-climb(대조)

같은 stub·같은 `--iters`·같은 시드로 3잡 돌려 **best holdout CER**·**iters-to-best** 비교. `llm`이 `random`/`best`를 holdout에서 못 이기면 "LLM 수선택은 기여 없음" — 이게 프로젝트가 내야 할 falsifiable 결과. archive가 절대 prune 안 되고 모든 행이 `parents`+diff를 가지므로 사후 lineage 완전 재구성 가능(귀속 위생).

---

## 8. 운영자 제어 (knob ≤6, 예측가능)

| knob | 효과 |
|---|---|
| `--iters N` | 정확히 N개 후보 생성. (현재의 `iters*3+10` 숨은 배수 없음.) |
| `--directive "<text>"` | 매 프롬프트 고정 슬롯에 그대로 주입. 탐색 *조향* 수단("VAD 세그먼트 시도", "beam 튜닝 그만"). |
| `--explore P` | iter가 EXPLORE(best서 발산)일 확률. 스칼라 하나 — decay 곡선·accumulator 없음. 1.0=항상발산, 0.0=항상refine. |
| `--ban "<substr>"` / `--pin <id>` | ban=프롬프트 "하지 마라" 블록에 줄 추가. pin=EXPLOIT 부모 고정. 둘 다 순수 프롬프트 텍스트, 숨은 상태 0. |
| `--holdout-every K` | new-best K회마다 holdout 자동 평가(0=잡 끝에만). |
| `--parent-policy` | llm/random/best (§7 ablation). |

**iter당 로그 1줄** (+ `<job>_log.jsonl` 1줄):
```
iter 037 | EXPLORE | parent=0012(0.1631) | move="two-pass bidir decode" | cer=0.1588 | KEEP (Δ-0.0043) | hold=0.1601
```
mode·parent는 루프가 기계적으로 정한 입력, move·learned는 LLM이 말한 것 → 한 줄이 완전한 인과 설명. `python -m scripts.archive_summary --job <id>`로 리더보드(in-loop+holdout 나란히).

---

## 9. 이행 계획 (단계적, big-bang 아님)

새 루프는 옛 코드 *옆에* 두고 인프라 재사용. holdout에서 이기기 전까진 옛 모듈 삭제 안 함.

- **Phase 0 — 스캐폴드**: `harness/candidate_cli.py`(하드닝/파싱 lift), `harness/evolve_simple.py`(~200줄), `scripts/evolve_simple.py`, `harness/prompts/candidate_simple.md`(사본).
- **Phase 1 — 인프라 재사용 스모크**: `--iters 1` 스텁서 1회 → 프롬프트·하드닝·verify·archive행·workspace복원 확인. scope 체크가 transcribe.py 외 변경을 reject하는지. `<job>_state.json`에 best_hyp_id 있는지(holdout 앵커).
- **Phase 2 — 병렬 비교(=실험)**: 같은 스텁서 4잡 — old controller / new(llm) / new(random) / new(best). 각자 holdout 평가. **결정 게이트**: new(llm)이 (a) old보다 holdout 우수 AND (b) 자기 random/best 대조 우수여야 cutover. (b) 안 되면 "단순화는 성공, LLM 수선택은 미입증" — 정직히 보고(여전히 단순성 승).
- **Phase 3 — cutover**: candidate_simple→candidate, scripts/evolve.py를 새 루프로(옛건 runner_legacy로 1주기 보존), AGENTS.md/SSOT 갱신.
- **Phase 4 — 옛 모듈 은퇴**: scheduler/lineage/portfolio/promotion/cooldown/policy/signature/gitops + 테스트 삭제(또는 history-archive). config.py는 `RUNTIME_HARD_MULTIPLIER`·`KEEP_DELTA_EPS`·verify timeout 정도만 남김.

**이행이 쉬운 이유**: `run_verify`/`evaluate_batch`/`guards.run_checks`가 이미 순수 함수, 하드닝은 ~80줄 섬, 샌드박스는 controller 무관, per-iter dir 레이아웃이 이미 flat archive가 원하는 형태. **어려운 점(미리 표시)**: holdout 앵커용 state 필드, candidate.md의 mode/family 문구를 삭제와 lockstep 편집, runner의 유용한 헬퍼가 scheduler와 엉켜 있으니 import 말고 lift, `EVOLVE_NO_HARDEN_CLAUDE` production 가드를 새 main으로 이전.

---

## 10. 운영자가 정할 열린 결정

1. **앙상블 1급 지원?** §5 `base_systems/` 스냅샷 + frozen helper를 지금 넣을지(0.154 앙상블 경로 직접 겨냥) vs 단일시스템 탐색 먼저 보고 나중에.
2. **다양성 구조**: flat + diversity-aware sampling 으로 시작(권장) vs 처음부터 MAP-Elites cell / island. (예산 150엔 flat 권장.)
3. **부모 선택 기본값**: `--parent-policy llm` 으로 시작 vs `weighted_random`(DGM식). ablation으로 어차피 비교.
4. **매 iter seed file**: 항상 best 복원 vs LLM이 지명한 parent 복원 vs 둘 다 인라인으로 주고 LLM이 새로 작성.
5. **ledger 성장 관리**: 수백 iter 후 `what_i_learned` 폭주 → 윈도우 N vs 주기적 LLM 요약 vs fingerprint cluster당 1개.
6. **holdout cadence**: new-best마다 vs 잡 끝에만.

---

## 부록 — 참고된 보조 보고서
- `docs/proposals/2026-06-06-diversity-convergence-strategy.md` (다양성·수렴 상세, 에이전트 B 작성)
- 메모: [[phase3_030-diversity-cultivation]], [[harness-single-head-c2]]
