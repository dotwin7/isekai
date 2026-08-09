# Runtime Live Smoke

- 설명: 실제 Agent host가 프로젝트 로컬 ISEKAI Adapter를 발견하고 Core lifecycle을 구동하는지 검증하는 절차
- 문서 역할: [ISEKAI canonical 문서 집합](isekai.md)의 일부

## 검증 경계

일반 테스트는 원격 모델을 호출하지 않는다. `tests/test_reference_product_e2e.py`가 설치된 launcher를 통해 전체 Unit을 결정론적으로 검증하고, `scripts/live-smoke.py`는 새 임시 프로젝트의 설치·초기화·doctor와 Runtime surface를 재현한다.

```bash
uv run python scripts/live-smoke.py
```

실제 host 호출은 인증·네트워크·비용이 필요하므로 opt-in이다.

```bash
uv run python scripts/live-smoke.py --runtime codex --host codex
uv run python scripts/live-smoke.py --runtime claude --host claude
uv run python scripts/live-smoke.py --runtime kiro --host kiro
```

모델 인증 없이 호스트의 Plugin/Skill 구조와 CLI capability만 검증하려면 별도 checker를 사용한다. Claude 검증은 strict manifest validation과 inline Plugin discovery를, Kiro 검증은 Skill frontmatter와 slash/headless capability를 확인한다.

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

실제 host를 지정하지 않은 관찰은 `surface-only`로 기록되며 live baseline의 근거가 아니다. `live-verified` 관찰만 `compatibility.json`의 `tested_versions`를 뒷받침할 수 있다.

Live smoke의 성공 기준은 단순히 Skill 파일이 존재하는 것이 아니다.

1. 새 Project에 Plugin package와 repo/project Skill이 설치되고 `doctor.ready=true`여야 한다.
2. 새 host 세션의 초기 Skill 목록에 ISEKAI가 주입되어야 한다. 모델이 프로젝트를 검색해 문서를 수동 발견한 경우는 실패다.
3. 명시적 `on`이 Project-local launcher로 `handshake`와 Core `on`을 호출해야 한다.
4. 같은 대화의 다음 일반 요청은 명령 재호출 없이 `intake`되어야 한다.
5. Unit Golden Path는 `status`, `resume`, `verify`를 호출하고 실제 `verify.valid`를 보고해야 한다.
6. 읽기 전용 smoke는 Unit이나 제품 파일을 만들거나 수정해서는 안 된다.

## 2026-08-08 관찰 결과

| Runtime | 로컬 버전 | 결과 | 근거 |
|---|---:|---|---|
| Codex | `0.147.0` | live verified | repo Skill 주입, `handshake/on`, 두 턴 자동 `intake`, 완성 Unit `status/resume/verify`와 `valid=true` |
| Claude Code | `2.1.224` | validation only | source와 설치 package의 `claude plugin validate` 통과; 로컬 CLI 미인증으로 모델 세션은 미실행 |
| Kiro | 없음 | unavailable | 로컬 앱과 CLI가 없어 실제 host smoke 미실행 |

초기 Codex 실험에서는 `.agents/plugins/marketplace.json`과 Plugin package만 배치했을 때 Skill이 세션에 주입되지 않았다. 모델이 명령 문자열을 보고 `.isekai`를 검색해 Skill 문서를 수동으로 읽었으므로 실패로 판정했다. 공식 local Skill 검색 위치인 `.agents/skills/isekai`를 설치하고 package와 별도의 digest로 lock에 결박한 뒤 같은 검증이 통과했다.

Claude Code도 project marketplace 선언만으로 무조건 설치되는 것으로 취급하지 않는다. Repository trust 뒤 marketplace와 Plugin 설치 동의가 적용되므로, 프로젝트 기본 Adapter는 `.claude/skills/isekai`에서 직접 발견하고 완전한 Plugin package는 별도 배포 surface로 보존한다.

Kiro CLI의 slash command는 interactive session 전용이다. Headless smoke는 첫 줄의 `ISEKAI_HEADLESS:` marker로 Workspace Skill을 명시적으로 활성화하고 `read,shell`만 사전 허용한다. 이 실행 방식은 lifecycle의 사람 승인을 대신할 수 없으며 `human_gate`에서 중단해야 한다.

## 2026-08-09 host contract observation

Claude Code `2.1.224`에서 source Plugin의 strict validation과 `--plugin-dir` 세션 발견을 다시 확인했다. Plugin은 `isekai-agent-plugin@inline`으로 enable되어 있었고 `isekai` Skill 하나를 노출했다. CLI 인증이 없어서 실제 모델 대화는 실행하지 않았으므로 상태는 계속 `validation-only`다.

Kiro는 로컬 CLI가 없어 source surface만 검증했다. 이후 GitHub Actions가 공식 current-stable installer로 Kiro CLI `2.16.2`를 설치하고 `.kiro/skills/isekai/SKILL.md`, CLI `2.1.0+` 요구사항, `--no-interactive`와 `--trust-tools` capability를 확인했다. 인증된 모델 세션은 실행하지 않았으므로 `validation-only`이며 live baseline은 아니다. 원시 실행 로그는 [Kiro workspace Skill contract job](https://github.com/dotwin7/isekai/actions/runs/31293230838/job/93193979111)에 연결한다.

## 판정 규칙

`compatibility.json`의 `tested_versions`에는 host validator만 통과한 버전을 넣지 않는다. 실제 모델이 Adapter를 발견하고 Core Golden Path를 수행한 버전만 live baseline으로 기록한다. 인증 부재, CLI 부재, host trust 거부는 Adapter 실패와 구분하여 `validation only` 또는 `unavailable`로 남긴다.

이전 저장소 문서에 검증 기준으로 쓰였지만 연결된 실행 Evidence가 없는 버전은 삭제해 역사를 감추지 않고 `legacy_versions`와 `unlinked-legacy` 관찰로 이동한다. 이는 live 검증 주장이 아니다.
