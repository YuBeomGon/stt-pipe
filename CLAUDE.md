# CLAUDE.md

> **Candidate session gate**: 본 prompt 본문이
> `=== BEGIN CANDIDATE PROFILE ===` 로 시작하면 (= `claude -p` 가
> `harness.runner.build_candidate_prompt` 결과를 받은 경우), **본 CLAUDE.md
> 전체를 무시한다**. Candidate 의 행동은 prompt 안의 profile
> ([`harness/prompts/candidate.md`](harness/prompts/candidate.md)) 와 그 뒤에
>이어지는 runtime 데이터만 따른다.
>
> 본 파일은 *interactive 운영자* (사람이 `claude` 를 직접 띄운 세션) 한정.
> 자세한 출처: [`docs/CANDIDATE-CONTEXT.md`](docs/CANDIDATE-CONTEXT.md).

기본 행동 규약 + 권한 표 + 정보 출처 + Phase 별 행동은 [`AGENTS.md`](AGENTS.md).
본 파일은 interactive 운영자 세션 한정 보충.

---

## 채팅 응답 스타일 (운영자 interactive 한정)

- 응답은 **짧고 간결**. 장황한 설명·중복 요약·불필요한 머리말 금지.
- 한 줄로 끝낼 수 있으면 한 줄로. 결정·결과·다음 한 단계만.
- **이 규칙은 interactive 운영자 채팅 한정**. 코드·문서 작성 (`docs/`,
  `README.md`, 코드 주석 등) 또는 candidate session (`claude -p`) 응답에는
  적용 X.
