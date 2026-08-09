# Unit과 인간 게이트

- 설명: Unit 구조, lifecycle, Decision·Evidence·Execution Envelope 계약과 원장 동시성
- 문서 역할: [ISEKAI canonical 문서 집합](isekai.md)의 일부

## Unit 구조와 lifecycle

Unit의 기계 식별자와 사람이 읽는 제목은 분리한다. `unit.json.id`와 Unit
디렉터리는 `UNIT-YYYYMMDD-<UUID>` 형식의 ASCII 값이며 CLI·경로·로그·외부
도구가 사용한다. `unit.json.title`은 사람이 Unit을 찾고 검토하는 표시
이름이므로 Project의 `document_language`를 따른다. 제목을 변경해도 Unit
identity는 바뀌지 않는다.

Unit은 의도에서 운영 결과까지 추적하는 정식 AI-DLC 작업 단위다.

```text
unit/
├─ unit.json
├─ intent.md
├─ requirements.md
├─ decisions.json
├─ amendments.json
├─ architecture.md
├─ implementation-guide.md
├─ plan.md
├─ acceptance.md
├─ evaluations/criteria.json
├─ evidence/
│  ├─ verification.json
│  └─ records/EVD-*.json
├─ checkpoint.json
├─ context-receipt.json
├─ execution-envelope.json
├─ execution-authorizations.json
├─ execution-authorization-records/ENV-*.json
├─ release.md
└─ operations.md
```

Unit 상태는 다음을 기본으로 한다.

```text
proposed
→ inception
→ awaiting-inception-decision
→ construction
→ validation
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
  "gate": "inception|architecture|release|operation|amendment|knowledge",
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
  "decided_at": "2026-08-04T00:00:00+00:00",
  "previous_decision_digest": null,
  "decision_digest": "sha256:..."
}
```

Core는 lifecycle을 임의로 건너뛰는 전이를 허용하지 않는다.

```text
proposed → inception → awaiting-inception-decision
→ construction → validation → awaiting-release-decision → releasing
→ operating → learned
```

`construction` 진입에는 승인된 Inception Decision, `validation` 진입에는 승인된 Architecture Decision, `releasing` 진입에는 승인된 Release Decision과 passing verification Evidence, `learned` 진입에는 승인된 Operation Decision이 필요하다. `awaiting-release-decision`은 Validation을 완료한 뒤에만 진입할 수 있다. 같은 게이트의 최신 Decision이 `rejected`이면 승인으로 간주하지 않는다.

`status`와 `resume`은 다음 전환의 승인 경계를 기계가 읽을 수 있는 `human_gate`로 반환한다. `next_transition`, `gate`, `decision`, `latest_decision_id`, `review_round`, `revision_requested`, `reconfirmation_required`, `blocks_next_transition`, `confirmation_required`, `confirmation_channel`, `core_identity_verification`을 포함하며, 필요한 Decision이 없으면 Adapter는 packet을 사람에게 보여주고 중단해야 한다. 호스트의 도구 실행 권한이나 headless trust 설정은 이 확인을 충족하지 않는다.

Human Gate는 일회성 확인이 아니라 반복 가능한 검수 루프다. 사용자가 제시된 packet 자체를 명시적으로 거부하면 현재 `review_round`의 `rejected` Decision으로 기록한다. 거부가 아니더라도 Unit이 `learned`가 되기 전에 수정·보완·추가·삭제를 요청하면 같은 Unit의 `amendments.json`과 승인된 Amendment Decision에 기록하고, 영향받는 문서와 구현을 고친 뒤 새 packet을 제시한다. 과거 승인을 수정 결과에 재사용하거나 피드백 반영 직후 완료로 종료해서는 안 된다. 피드백이 scope·stage plan·risk·필요 action을 바꾸면 Execution Envelope도 교체하고 새 Inception Decision을 받아야 한다.

Decision은 해당 게이트를 실제로 검토할 수 있는 lifecycle 상태에서만 기록한다. Inception Decision은 `awaiting-inception-decision`과 Envelope 갱신·철회를 위한 Construction·Release·Operations 상태에서, Architecture Decision은 `construction`, Release Decision은 `awaiting-release-decision` 또는 Release 단계에서 Evidence를 갱신한 `releasing`, Operation Decision은 `operating`에서만 허용한다. 승인된 Release Decision은 현재 passing Verification Evidence의 ID와 digest를 `approval_subject`로 결박하므로 Evidence보다 먼저 선승인하거나 Evidence 교체 뒤 재사용할 수 없다. `decision_digest`는 감사 대상인 Decision 전체를 정규화해 계산하고 `previous_decision_digest`는 직전 Decision을 연결한다. Core는 전체 원장의 digest chain, 고유 ID와 엄격히 증가하는 `decided_at`을 검증하므로 레코드 변경이나 재정렬이 발견되면 해당 원장을 무효로 처리한다.

Unit 생성 전 Level-1 plan 승인과 lifecycle Decision은 역할이 다르다. 전자는 Agent가 제안한 전체 작업을 시작해도 되는지 확인하고, 후자는 확정된 artifact의 ID와 digest를 다음 상태 전이에 결박한다. 최초 계획에 최종 Inception packet과 정확한 Envelope가 함께 제시되었다면 한 번의 명시적 사용자 응답을 두 기록의 근거로 사용할 수 있지만, 승인 뒤 내용이 달라졌다면 새 Decision이 필요하다.

Unit의 종료 경계는 구현 완료 선언이 아니라 승인된 Operation Decision 뒤의 `learned` 상태다. 그 전까지 사용자의 후속 대화는 기본적으로 같은 active Unit에 속한다. `amend --request ... --affected-artifact ... --requested-by ...`는 정확한 요청, 영향 문서의 변경 전 digest, 필요한 gate, 요청자와 시각을 append-only `amendments.json`에 기록하고 같은 digest를 Amendment Decision에 결박한다. Intent·Requirements·Plan·Acceptance 변경은 Inception, Architecture·Implementation Guide 변경은 Construction, Release 변경은 Validation, Operations 변경은 Operating으로 같은 Unit을 필요한 만큼 되돌리며 현재 Verification Evidence를 무효화한다.

Core는 `unit-init` 또는 `resume` 시 하나의 unfinished Unit을 Project-scoped active Unit으로 결박하고 ignored `.isekai-runtime/active-unit.json`의 digest chain에 bind 사건을 기록한다. 이 결박이 있으면 새 `intake`·`route`·`inception`·`unit-init`과 형제 Unit을 대상으로 한 persistent Runtime action은 거부된다. `off`나 새 대화는 결박을 풀지 않으며 `learned` 전환만 이를 기계적으로 완료한다. 사용자가 unfinished Unit을 남기고 별도 작업·포기·전환을 명시적으로 선택한 경우에는 현재 Checkpoint가 authorization progress와 일치해야 하며, `active-unit-detach --unit ... --requested-by ... --reason ...`가 detach 사건을 기록한 뒤에만 다른 경로를 열 수 있다.

Amendment가 열린 동안에는 영향받는 문서의 digest가 실제로 바뀌고 새 gate Decision의 `references`가 amendment ID를 포함해야 다시 전진할 수 있다. 따라서 이 대화처럼 단순 추가 요구도 `rejected`가 아니면서 문서·Decision·Checkpoint에 남고, 구현만 바꾼 뒤 이전 승인으로 통과할 수 없다. 여러 종류의 문서가 함께 영향을 받으면 가장 이른 gate를 다시 거친다. 이미 `learned`인 Unit은 수정하지 않고 새 Unit으로 시작한다.

`unit-init`은 누락을 눈에 띄게 만들기 위한 `ISEKAI:placeholder` template을 생성할 뿐 계획을 영속화한 것으로 간주하지 않는다. Agent는 계획 승인 직후 첫 lifecycle transition 전에 승인된 내용을 `intent.md`, `requirements.md`, `plan.md`, `acceptance.md`와 Release·Operations disposition 및 Checkpoint에 기록해야 한다. Core는 placeholder나 필수 단계가 빠진 계획을 materialized artifact로 인정하지 않으며 `inception` 진입을 거부한다. Construction에서는 실제 구현과 함께 `architecture.md`, `implementation-guide.md`를 완성한 뒤 Architecture Decision을 받아야 Validation으로 전이할 수 있다.

새 승인 Decision은 게이트별 Unit artifact snapshot을 기록한다. Inception은 Intent·Requirements·Plan·Acceptance Criteria, Architecture는 Architecture·Implementation Guide, Release는 Release 문서, Operation은 Operations 문서의 SHA-256 digest에 결박된다. Architecture 이후 snapshot은 해당 게이트까지 발급된 `edit`·`test`·`external-api` authorization progress cursor도 포함한다. 따라서 승인 뒤 같은 단계에서 구현이나 검증 작업이 추가되면 checkpoint를 갱신했더라도 새 Decision이 필요하다. Acceptance의 체크 상태만 완료로 바뀌는 것은 Inception 의도를 변경하지 않도록 정규화하지만 기준 문구가 달라지면 새 Inception Decision이 필요하다. 이전 Runtime에서 생성되어 snapshot이 없거나 version 1.0인 Decision은 조회 호환성을 유지하되, 새 Decision부터 이 결박을 기록한다.

Core는 활성·보관 authorization ledger의 `edit`·`test`·`external-api` grant를 누적한 progress cursor를 `checkpoint.json`에 기록한다. 해당 action의 authorization 응답은 `checkpoint_required: true`를 반환한다. 이후 checkpoint가 없으면 lifecycle transition과 승인 Decision이 차단되고, `status`의 `checkpoint_progress` 및 `resume`의 `checkpoint_fresh`·`checkpoint_issues`·`recovery_required`가 중단 복구 필요성을 표시한다. Agent는 작업 batch 직후 관련 Architecture·Implementation Guide·Acceptance·Release·Operations artifact와 Checkpoint를 함께 갱신해야 하며, 승인된 Intent·Plan을 단순 진행 표시 용도로 고치지 않는다.

`releasing` 진입은 필수 Unit artifact, 체크된 acceptance criteria, blocker 없는 checkpoint까지 확인한다. `learned` 진입은 이 조건에 더해 현재 passing Evidence와 빈 `pending` 목록을 요구하므로 완료되지 않은 Unit이 종료 상태를 가질 수 없다.

## Verification Evidence

Verification Evidence는 실행 결과를 재현할 수 있도록 다음 최소 구조를 갖는다.

```json
{
  "id": "EVD-...",
  "type": "verification-evidence",
  "schema_version": "1.0.0",
  "unit_id": "UNIT-...",
  "stage": "validation",
  "passed": true,
  "scope": "Core and runtime Golden Path",
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

Evidence는 명령·exit code·결과 digest·관찰 시각·범위·기록 주체와 당시 Execution Envelope·authorization 원장 digest를 보존해야 한다. 각 command는 같은 stage의 `managed-test`가 실행과 함께 만든 최신 `test` grant의 `authorization_id`를 고유하게 참조한다. 승인 전, Construction 진입 전, non-test grant, 오래된 grant 또는 grant 뒤 edit가 있는 Evidence는 거부한다. 정식 release 검증은 `validation` lifecycle 상태와 stage에서 실행한다. Evidence를 기록하면 현재 상태는 `evidence/verification.json`에 갱신하고 같은 내용을 ID별 불변 레코드 `evidence/records/EVD-*.json`에도 보존한다. Release Decision은 이 레코드의 경로·ID·digest를 결박하므로 Operations Evidence가 현재 파일을 교체한 뒤에도 과거 Release 승인을 검증할 수 있다. Evidence를 기록한 뒤 새 authorization grant가 추가되거나 Envelope가 교체되면 현재 Evidence는 stale로 판정한다. Release Decision만 있거나 현재 authorization 상태에 결박된 passing Evidence가 없으면 `releasing` 전이를 허용하지 않는다.

`managed-test`는 원본 Project를 일회용 workspace로 복제해 그 안에서 명령을 실행하고 exit code와 stdout/stderr digest를 authorization grant의 `execution` receipt에 결박한다. 상대 경로로 생성·수정된 테스트 파일은 원본 Project에 반영되지 않는다. 외부 CI나 별도 runner가 만든 Evidence도 허용되며, 이 경로의 `attestation`은 원본 `output`이 전달된 명령과 digest만 전달된 명령을 `core-derived`, `caller-supplied`, `mixed`로 구분한다. Core는 어느 경로에서도 actor의 실제 신원을 인증하지 않는다. 외부 실행 사실까지 보증해야 한다면 보호된 CI, 서명된 host receipt 또는 별도 원격 실행 시스템의 검증 결과를 함께 사용한다.

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
    },
    {
      "name": "validation",
      "depth": "standard",
      "allowed_actions": ["read", "test"]
    }
  ],
  "allowed_actions": ["read", "edit", "test"],
  "forbidden_actions": ["remote", "deploy", "credential-access"],
  "external_access": [],
  "max_iterations": 5,
  "approval_digest": "sha256:...",
  "approval_decision_id": "DEC-...",
  "approval_decision_digest": "sha256:..."
}
```

Inception Decision은 Envelope의 고유 ID와 `approval_digest`를 함께 결박하고, Envelope는 승인 당시의 `decision_digest`를 `approval_decision_digest`로 보존한다. 이후 Envelope나 Decision이 변경되면 다시 사람의 승인을 받아야 한다. 읽기와 외부 API는 `authorize`가 grant를 기록하지만, `edit`와 `test`는 authorization과 실제 실행을 분리하지 않고 각각 `managed-edit`와 `managed-test`가 수행한다. multi-target edit receipt는 `targets`와 파일별 before/after digest를, test receipt는 command·exit code·output digest를 grant의 `execution`에 결박한다. Unit 문서는 별도 `artifact-write`로만 바꾸며 이미 승인된 의미를 바꾸려면 pending Amendment가 필요하다. `acceptance.md`의 기존 항목을 `[ ]`에서 `[x]`로만 진행하는 변경은 허용하지만 텍스트 변경이나 체크 해제는 이 예외에 포함하지 않는다. grant가 있는 Envelope를 교체하면 Core는 이전 Envelope와 authorization ledger를 `execution-authorization-records/ENV-*.json`에 digest-bound 불변 레코드로 보존한다. 저장된 grant를 다시 검증할 때도 target을 현재 파일시스템 기준으로 resolve하며, 승인 뒤 symlink가 Project 외부 또는 control path로 바뀌면 즉시 원장을 거부한다.

Context Receipt의 `maximum_agent_level`은 Envelope가 허용할 수 있는 action의 상한이다. `L0`은 `read`, `L1`은 `read`, `edit`, `test`, `L2`는 여기에 `external-api`를 추가한다. Core는 Envelope 제안·승인·authorization·Unit 검증에서 이 상한을 다시 확인하므로, 낮은 level의 Project에서 더 넓은 action을 적어 넣어도 권한이 생기지 않는다.

L2 Envelope는 `external-api`와 함께 비어 있지 않은 `external_access`를 가져야 한다. 각 항목은 `id`, `credential_ref`, `environment`, `scheme`, `host`, `path`, `methods`, `max_requests`만 허용하며, `credential_ref`는 `secret://provider/name`, 환경은 `development|test`, scheme은 `https`로 제한된다. 실제 key 값이나 token 필드는 거부한다. 외부 authorization grant는 정책 ID·환경·method·reference를 원장에 기록하고 정책별 요청 예산도 별도로 소모한다. Evidence command가 외부 통합을 검증했다면 선행 grant ID를 `external_authorization_ids`에 기록한다.

`scope` 패턴은 디렉토리 경계를 존중한다. `*`와 `?`는 한 경로 세그먼트 안에서만 매칭하고, 세그먼트 전체가 `**`일 때만 0개 이상의 세그먼트를 가로지른다. 예를 들어 `src/*.py`는 `src/main.py`만 허용하고 `src/vendor/deep.py`는 스코프 밖이다. 하위 트리 전체를 허용하려면 `src/**`처럼 명시해야 한다.

### Unit 실행 격리

Execution Envelope는 선택된 Unit 하나에만 속한다. `scope: ["**"]`처럼 넓은 패턴이 승인되어도 그 Envelope로 기본 `units/` collection이나 형제 Unit의 artifact에 대한 `read`, `edit`, `test` authorization을 받을 수 없다. Project 내부의 사용자 지정 Unit 경로도 유효한 canonical Unit ID를 가진 `unit.json`이 있으면 디렉터리 이름이 바뀐 뒤에도 같은 경계를 적용한다. 활성 Unit으로 선택하려면 canonical Unit ID와 디렉터리 이름이 일치해야 한다. 저장된 grant는 `verify`에서 현재 파일시스템 기준으로 다시 검사하므로, 승인 후 symlink가 형제 Unit으로 바뀐 경우에도 원장을 무효화한다.

이 경계는 filesystem sandbox가 아니다. 현재 Unit의 작업에는 프로젝트 소스와 테스트, 고정 Foundation·Profile·Extension이 필요하므로 이들은 Context Receipt와 승인 Envelope 범위 안에서 계속 접근할 수 있다. 다른 Unit의 작업을 이어갈 때는 현재 Checkpoint를 보존하고 사용자의 명시적 판단으로 `active-unit-detach`를 기록한 뒤 해당 Unit을 `resume --unit PATH`하여 active Unit을 교체한다. 형제 Unit의 결과가 공통 입력으로 필요하면 [Project Knowledge](project-knowledge.md)의 후보→사람 승인→승격 흐름이나 후속 명시적 참조 계약을 사용해야 하며, 현재 Unit이 형제 Unit 원장을 암묵적으로 탐색하지 않는다. `project-knowledge/`는 Core-managed path이므로 Unit Envelope로 직접 읽지 않고 Receipt에 고정된 release만 소비한다.

### Envelope 갱신

`expires_at`은 승인이 새 action을 허가하는 창을 한정한다. 기본 창은 168시간이며 `--expires-in-hours`로 최대 720시간까지 조정한다. 창이 닫히거나 `max_iterations` 예산이 소진되면 Unit을 폐기하지 않고 Envelope를 갱신한다.

```text
envelope-propose --unit PATH ...      # 교체 Envelope를 proposed 상태로 기록하고 ledger를 초기화
decision --gate inception --outcome approved --reference execution-envelope.json ...
envelope-approve --unit PATH          # 새 Decision에 결박해 활성화
```

교체 Envelope는 `proposed` 상태로 시작하므로, 새 Decision이 승인하기 전까지 Unit은 어떤 authorization도 보유하지 않는다. 즉 갱신은 만료를 우회하는 경로가 아니라 사람의 승인을 다시 요구하는 경로다. 갱신은 Construction과 Validation뿐 아니라 `releasing`과 `operating`에서도 가능하다. Release 단계에서 새 grant와 Evidence를 만든 경우에는 현재 Evidence에 결박된 Release Decision도 다시 기록해야 Operations로 전이할 수 있다. 만료는 authorization 시점에만 판정하며, `verify`는 Envelope의 구조와 결박만 감사하므로 승인 창이 닫힌 뒤에도 Unit 기록은 계속 검증 가능하다.

## 원장 동시성

`decisions.json`과 `unit.json`은 read-modify-write 원장이다. 여러 세션·런타임이 같은 Unit을 다룰 수 있으므로, Unit의 모든 변경(Decision, transition, Envelope 제안·승인, Evidence, checkpoint, authorization)은 Unit 단위 파일 락으로 직렬화한다. `verify`, `status`, `resume`도 같은 락 아래에서 여러 Unit artifact의 일관된 snapshot을 읽는다. Foundation release Decision도 같은 방식으로 Foundation 단위 락을 사용한다. Scope wildcard matching은 반복된 `**`가 같은 suffix를 다시 탐색하지 않도록 동적 계획법으로 제한해, 승인된 패턴이 authorization을 지수 시간 동안 점유하지 않게 한다.

락은 운영체제의 advisory file lock으로 획득한다. 프로세스가 비정상 종료되면 운영체제가 lock을 해제하므로 방치 파일의 시각을 추측하거나 경쟁적으로 삭제하지 않는다. lock path는 single-link regular file만 허용하고, 대기 중 열어 둔 inode가 교체된 경우에는 현재 lock path와 같은 파일인지 재확인한 뒤에만 임계 구역에 들어간다. 락을 잡지 못하면 짧게 대기하고, 그래도 실패하면 덮어쓰지 않으며 CLI는 traceback 대신 구조화된 오류를 반환한다.

Decision 기록의 postflight는 "내 레코드가 마지막인가"가 아니라 "이전 레코드가 모두 보존된 채 내 레코드가 추가되었는가"를 확인한다. 전자는 남의 레코드를 덮어쓴 writer도 통과시킨다.

승인된 Envelope 밖의 Scope·Action·Stage는 Core가 fail-closed로 거부한다. Envelope가 없거나 불완전하면 Agent는 제안·읽기 수준에 머물며, 원격·운영·Credential Action은 기본 금지한다. 이 구조는 고정된 workflow를 강제하기보다 Unit의 Intent·위험·복잡도에 따라 Agent가 단계와 깊이를 제안하고 사람이 승인하는 Adaptive AI-DLC를 지원한다.
