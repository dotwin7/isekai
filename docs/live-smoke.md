# Runtime Live Smoke

- 설명: 실제 Agent host가 프로젝트 로컬 ISEKAI Adapter를 발견하고 Core lifecycle을 구동하는지 검증하는 절차
- 문서 역할: [ISEKAI canonical 문서 집합](isekai.md)의 일부

## 검증 경계

일반 테스트는 원격 모델을 호출하지 않는다. `tests/test_reference_product_e2e.py`가 설치된 launcher를 통해 전체 Unit을 결정론적으로 검증하고, `scripts/live-smoke.py`는 새 임시 프로젝트의 설치·초기화·doctor와 Runtime surface를 재현한다. 실제 host를 선택하면 별도의 완성 Reference Project도 준비하고 먼저 Core `verify.valid=true`인지 확인한다.

```bash
uv run python scripts/live-smoke.py
```

실제 host 호출은 인증·네트워크·비용이 필요하므로 opt-in이다.

```bash
uv run python scripts/live-smoke.py --runtime codex --host codex
uv run python scripts/live-smoke.py --runtime claude --host claude
uv run python scripts/live-smoke.py --runtime kiro --host kiro
uv run python scripts/live-smoke.py --runtime all --host all
```

모델 인증 없이 호스트의 Skill 구조와 CLI capability만 검증하려면 별도 checker를 사용한다. 기본 검사는 세 프로젝트 Skill을 확인하며, Kiro 검증은 Skill frontmatter와 slash/headless capability를 확인한다.

```bash
uv run python scripts/runtime-host-check.py --runtime all
uv run python scripts/runtime-host-check.py --runtime claude --require-cli
uv run python scripts/runtime-host-check.py --runtime kiro --require-cli
```

감사 가능한 JSON 레코드가 필요하면 결과를 현재 `distribution/release.json`의 SHA-256 digest에 결박해 저장한다. `recorded_by`는 script가 인증한 신원이 아니라 호출자가 보고한 actor이며, 이 한계도 Evidence의 `attestation`에 기록된다.

```bash
uv run python scripts/live-smoke.py \
  --runtime codex --host codex \
  --evidence-output evidence/runtime-smoke-codex.json \
  --recorded-by release-validator
```

`--host all`은 선택된 세 Runtime의 실행 파일과 인증을 모두 요구하며 하나라도 실제 검증에 실패하면 전체 실행이 실패한다. 실제 host를 지정하지 않은 관찰은 `surface-only`로 기록되며 live baseline의 근거가 아니다. `live-verified` 관찰만 `compatibility.json`의 `tested_versions`를 뒷받침할 수 있다.

Live smoke의 성공 기준은 단순히 Skill 파일이 존재하는 것이 아니다.

1. 새 Project에 선택한 repo/project/workspace Skill과 Core가 설치되고 `doctor.ready=true`여야 한다. 기본 설치에는 marketplace package나 선언이 없어야 한다.
2. 새 host 세션의 초기 Skill 목록에 ISEKAI가 주입되어야 한다. 모델이 프로젝트를 검색해 문서를 수동 발견한 경우는 실패다.
3. 명시적 `on`이 Project-local launcher로 `handshake`와 Core `on`을 호출해야 한다.
4. 같은 대화의 다음 일반 요청은 명령 재호출 없이 `intake`되어야 한다.
5. Unit Golden Path는 `status`, `resume`, `verify`를 호출하고 실제 `verify.valid`를 보고해야 한다.
6. 읽기 전용 smoke는 Unit이나 제품 파일을 만들거나 수정해서는 안 된다.

각 실제 host는 세 단계로 자동 검증된다. 첫 세션에서 `handshake/on`을 수행하고, 그 세션 ID 또는 해당 Project의 최근 세션을 재개해 ISEKAI·`intake`·MCP를 언급하지 않은 일반 후속 요청이 자동으로 `intake`되는지 확인한다. 마지막으로 완성 Reference Project의 새 세션에서 `status`, `resume`, `verify`를 호출하고 `learned`와 `verify.valid=true`를 확인한다. 모든 lifecycle action은 Project 실행 보호 설정이 연결한 `isekai-core` MCP `runtime_action`을 통한다. Codex는 JSONL의 `thread.started`와 MCP tool call, Claude는 stream JSON의 MCP tool-use와 Core 응답을 판정에 사용한다. Kiro는 생성된 `isekai-core` agent, directory-scoped `--resume`, required MCP startup을 사용하고, 모델 본문이 아니라 `KIRO_ACP_RECORD_PATH`의 ACP JSONL에서 실제 MCP 호출과 Core 결과를 판정한다. Evidence에는 각 단계의 trace 형식·digest·관찰된 MCP action이 결박된다.

이 자동화가 존재한다는 사실만으로 live baseline이 생기지는 않는다. 실제 인증 세션이 성공하고 digest-bound Evidence를 보존한 경우에만 `live-verified`로 기록한다.

## 2026-08-08 관찰 결과

| Runtime | 로컬 버전 | 결과 | 근거 |
|---|---:|---|---|
| Codex | `0.147.0` | live verified | repo Skill 주입, `handshake/on`, 두 턴 자동 `intake`, 완성 Unit `status/resume/verify`와 `valid=true` |
| Claude Code | `2.1.224` | validation only | project Skill source contract 통과; 로컬 CLI 미인증으로 모델 세션은 미실행 |
| Kiro | 없음 | unavailable | 로컬 앱과 CLI가 없어 실제 host smoke 미실행 |

Codex는 `.agents/skills/isekai`, Claude Code는 `.claude/skills/isekai`에서 프로젝트 Adapter를 직접 발견한다. ISEKAI 설치기는 marketplace package나 선언을 만들지 않는다.

Kiro CLI의 slash command는 interactive session 전용이다. Headless smoke는 첫 줄의 `ISEKAI_HEADLESS:` marker로 Workspace Skill을 명시적으로 활성화하고 `read,shell`만 사전 허용한다. 이 실행 방식은 lifecycle의 사람 승인을 대신할 수 없으며 `human_gate`에서 중단해야 한다.

## 2026-08-09 host contract observation

Claude Code `2.1.224`에서 project Skill source contract를 확인했다. CLI 인증이 없어서 실제 모델 대화는 실행하지 않았으므로 상태는 계속 `validation-only`다.

Kiro는 로컬 CLI가 없어 source surface만 검증했다. 이후 GitHub Actions가 공식 current-stable installer로 Kiro CLI `2.16.2`를 설치하고 `.kiro/skills/isekai/SKILL.md`, CLI `2.1.0+` 요구사항, `--no-interactive`와 `--trust-tools` capability를 확인했다. 인증된 모델 세션은 실행하지 않았으므로 `validation-only`이며 live baseline은 아니다. 원시 실행 로그는 [Kiro workspace Skill contract job](https://github.com/dotwin7/isekai/actions/runs/31293230838/job/93193979111)에 연결한다.

## 판정 규칙

`compatibility.json`의 `tested_versions`에는 host validator만 통과한 버전을 넣지 않는다. 실제 모델이 Adapter를 발견하고 Core Golden Path를 수행한 버전만 live baseline으로 기록한다. 인증 부재, CLI 부재, host trust 거부는 Adapter 실패와 구분하여 `validation only` 또는 `unavailable`로 남긴다.

이전 저장소 문서에 검증 기준으로 쓰였지만 연결된 실행 Evidence가 없는 버전은 삭제해 역사를 감추지 않고 `legacy_versions`와 `unlinked-legacy` 관찰로 이동한다. 이는 live 검증 주장이 아니다.
