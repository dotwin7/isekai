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
```

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

## 판정 규칙

`compatibility.json`의 `tested_versions`에는 host validator만 통과한 버전을 넣지 않는다. 실제 모델이 Adapter를 발견하고 Core Golden Path를 수행한 버전만 live baseline으로 기록한다. 인증 부재, CLI 부재, host trust 거부는 Adapter 실패와 구분하여 `validation only` 또는 `unavailable`로 남긴다.
