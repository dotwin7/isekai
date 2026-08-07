# Unit과 인간 게이트

- 설명: Unit 구조, lifecycle, Decision·Evidence·Execution Envelope 계약과 원장 동시성
- 문서 역할: [ISEKAI canonical 문서 집합](isekai.md)의 일부

## Unit 구조와 lifecycle

Unit은 의도에서 운영 결과까지 추적하는 정식 AI-DLC 작업 단위다.

```text
unit/
├─ unit.json
├─ intent.md
├─ requirements.md
├─ decisions.json
├─ architecture.md
├─ implementation-guide.md
├─ plan.md
├─ acceptance.md
├─ evaluations/criteria.json
├─ evidence/verification.json
├─ checkpoint.json
├─ context-receipt.json
├─ execution-envelope.json
├─ execution-authorizations.json
├─ release.md
└─ operations.md
```

Unit 상태는 다음을 기본으로 한다.

```text
proposed
→ inception
→ awaiting-inception-decision
→ construction
→ awaiting-release-decision
→ releasing
→ operating
→ learned
```

| 게이트 | 사람이 확인할 내용 |
|---|---|
| Inception Decision | 문제·범위·인수 조건·위험 |
| Architecture Decision | 도메인·Semantic·외부 계약 |
| Release Decision | 테스트·평가·미해결 위험·롤백 |
| Operation Decision | 고위험 실행·사고·권한 변경 |
| Knowledge Promotion | 지식의 근거·중복·유효기간 |

Unit의 위험과 Profile에 따라 산출물을 줄일 수 있지만 Decision과 Evidence를 생략해 중요한 게이트를 우회할 수는 없다.

## Decision

Decision은 Unit의 `decisions.json`에 다음 최소 구조로 기록한다.

```json
{
  "id": "DEC-...",
  "type": "human-decision",
  "schema_version": "1.0.0",
  "unit_id": "UNIT-...",
  "gate": "inception|architecture|release|operation|knowledge",
  "outcome": "approved|rejected",
  "summary": "사람이 승인하거나 거부한 판단의 요약",
  "scope": "project:security-product",
  "decision_packet_version": "1.0.0",
  "rationale": [
    "선택한 설계가 요구사항과 Scope에 맞는다."
  ],
  "alternatives": [
    {
      "option": "대안 설계",
      "reason": "선택하지 않은 이유"
    }
  ],
  "tradeoffs": [
    "얻는 것과 포기하는 것"
  ],
  "risks": [
    "남아 있는 위험"
  ],
  "references": [
    "requirements.md",
    "execution-envelope.json"
  ],
  "approval_subject": {
    "type": "execution-envelope",
    "id": "ENV-UNIT-...",
    "digest": "sha256:..."
  },
  "decided_by": "human-owner",
  "decided_at": "2026-08-04T00:00:00+00:00"
}
```

Core는 lifecycle을 임의로 건너뛰는 전이를 허용하지 않는다.

```text
proposed → inception → awaiting-inception-decision
→ construction → awaiting-release-decision → releasing
→ operating → learned
```

`construction` 진입에는 승인된 Inception Decision, `awaiting-release-decision` 진입에는 승인된 Architecture Decision, `releasing` 진입에는 승인된 Release Decision과 passing verification Evidence, `learned` 진입에는 승인된 Operation Decision이 필요하다. 같은 게이트의 최신 Decision이 `rejected`이면 승인으로 간주하지 않는다.

Decision은 해당 게이트를 실제로 검토할 수 있는 lifecycle 상태에서만 기록한다. Inception Decision은 `awaiting-inception-decision`과 Envelope 갱신·철회를 위한 Construction 상태에서, Architecture Decision은 `construction`, Release Decision은 `awaiting-release-decision`, Operation Decision은 `operating`에서만 허용한다. 승인된 Release Decision은 현재 passing Verification Evidence의 ID와 digest를 `approval_subject`로 결박하므로 Evidence보다 먼저 선승인하거나 Evidence 교체 뒤 재사용할 수 없다.

`releasing` 진입은 필수 Unit artifact, 체크된 acceptance criteria, blocker 없는 checkpoint까지 확인한다. `learned` 진입은 이 조건에 더해 현재 passing Evidence와 빈 `pending` 목록을 요구하므로 완료되지 않은 Unit이 종료 상태를 가질 수 없다.

## Verification Evidence

Verification Evidence는 실행 결과를 재현할 수 있도록 다음 최소 구조를 갖는다.

```json
{
  "id": "EVD-...",
  "type": "verification-evidence",
  "schema_version": "1.0.0",
  "unit_id": "UNIT-...",
  "stage": "construction",
  "passed": true,
  "scope": "Core and plugin Golden Path",
  "recorded_by": "validator",
  "recorded_at": "2026-08-04T00:00:00+00:00",
  "envelope_id": "ENV-UNIT-...",
  "envelope_digest": "sha256:...",
  "authorization_ledger_digest": "sha256:...",
  "authorization_count": 3,
  "commands": [
    {
      "command": "PYTHONPATH=src python3 -m pytest -q",
      "exit_code": 0,
      "output_digest": "sha256-hex-64-characters",
      "observed_at": "2026-08-04T00:00:00+00:00",
      "authorization_id": "AUTH-..."
    }
  ]
}
```

Evidence는 명령·exit code·결과 digest·관찰 시각·범위·기록 주체와 당시 Execution Envelope·authorization 원장 digest를 보존해야 한다. 각 command는 같은 stage에서 명령 직전에 발급된 최신 `test` grant의 `authorization_id`를 고유하게 참조한다. 승인 전, Construction 전, non-test grant, 오래된 grant 또는 grant 뒤 edit가 있는 Evidence는 거부한다. Evidence를 기록한 뒤 새 authorization grant가 추가되거나 Envelope가 교체되면 기존 Evidence는 stale로 판정한다. Release Decision만 있거나 현재 authorization 상태에 결박된 passing Evidence가 없으면 `releasing` 전이를 허용하지 않는다.

## Execution Envelope

Agent 실행은 Unit별 Execution Envelope로 제한한다. Agent는 Context와 Intent를 바탕으로 Envelope를 제안할 수 있지만, 사람의 Inception Decision이 `execution-envelope.json`을 참조해 승인하기 전에는 Construction을 시작할 수 없다.

```json
{
  "id": "ENV-UNIT-...",
  "type": "execution-envelope",
  "schema_version": "1.0.0",
  "unit_id": "UNIT-...",
  "status": "approved",
  "scope": ["src/**", "tests/**"],
  "stages": [
    {
      "name": "construction",
      "depth": "standard",
      "allowed_actions": ["read", "edit", "test"]
    }
  ],
  "allowed_actions": ["read", "edit", "test"],
  "forbidden_actions": ["remote", "deploy", "credential-access"],
  "max_iterations": 5,
  "approval_digest": "sha256:...",
  "approval_decision_id": "DEC-..."
}
```

Inception Decision은 Envelope의 고유 ID와 `approval_digest`를 함께 결박한다. 이후 Envelope가 교체되거나 변경되면 다시 사람의 승인을 받아야 한다. `authorize`는 Project 내부의 정규화된 target과 실제 Unit phase만 사용하고, 허용된 grant를 `execution-authorizations.json`에 기록하면서 `max_iterations` 예산을 소모한다.

`scope` 패턴은 디렉토리 경계를 존중한다. `*`와 `?`는 한 경로 세그먼트 안에서만 매칭하고, 세그먼트 전체가 `**`일 때만 0개 이상의 세그먼트를 가로지른다. 예를 들어 `src/*.py`는 `src/main.py`만 허용하고 `src/vendor/deep.py`는 스코프 밖이다. 하위 트리 전체를 허용하려면 `src/**`처럼 명시해야 한다.

### Envelope 갱신

`expires_at`은 승인이 새 action을 허가하는 창을 한정한다. 기본 창은 168시간이며 `--expires-in-hours`로 최대 720시간까지 조정한다. 창이 닫히거나 `max_iterations` 예산이 소진되면 Unit을 폐기하지 않고 Envelope를 갱신한다.

```text
envelope-propose --unit PATH ...      # 교체 Envelope를 proposed 상태로 기록하고 ledger를 초기화
decision --gate inception --outcome approved --reference execution-envelope.json ...
envelope-approve --unit PATH          # 새 Decision에 결박해 활성화
```

교체 Envelope는 `proposed` 상태로 시작하므로, 새 Decision이 승인하기 전까지 Unit은 어떤 authorization도 보유하지 않는다. 즉 갱신은 만료를 우회하는 경로가 아니라 사람의 승인을 다시 요구하는 경로다. 만료는 authorization 시점에만 판정하며, `verify`는 Envelope의 구조와 결박만 감사하므로 승인 창이 닫힌 뒤에도 Unit 기록은 계속 검증 가능하다.

## 원장 동시성

`decisions.json`과 `unit.json`은 read-modify-write 원장이다. 여러 세션·런타임이 같은 Unit을 다룰 수 있으므로, Unit의 모든 변경(Decision, transition, Envelope 제안·승인, Evidence, checkpoint, authorization)은 Unit 단위 파일 락으로 직렬화한다. Foundation release Decision도 같은 방식으로 Foundation 단위 락을 사용한다.

락은 `os.link`의 원자성으로 획득하고 inode 비교로 소유를 확인한다. hard link를 지원하지 않는 파일시스템에서는 고유 claim token을 exclusive-create한 lock에 기록해 같은 소유권을 확인한다. 이 확인이 없으면 두 프로세스가 같은 방치 락을 동시에 stale로 판정하고 각자 자기 락이라고 믿는 경쟁이 남는다. 락을 잡지 못하면 짧게 대기하고, 그래도 실패하면 덮어쓰지 않고 오류를 낸다. 5분이 지난 락은 프로세스가 죽은 것으로 보고 회수한다.

Decision 기록의 postflight는 "내 레코드가 마지막인가"가 아니라 "이전 레코드가 모두 보존된 채 내 레코드가 추가되었는가"를 확인한다. 전자는 남의 레코드를 덮어쓴 writer도 통과시킨다.

승인된 Envelope 밖의 Scope·Action·Stage는 Core가 fail-closed로 거부한다. Envelope가 없거나 불완전하면 Agent는 제안·읽기 수준에 머물며, 원격·운영·Credential Action은 기본 금지한다. 이 구조는 고정된 workflow를 강제하기보다 Unit의 Intent·위험·복잡도에 따라 Agent가 단계와 깊이를 제안하고 사람이 승인하는 Adaptive AI-DLC를 지원한다.
