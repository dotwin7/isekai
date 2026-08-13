# Agent Control Plane — 폐기된 Unit 재사용 초안

- 설명: 외부 에이전트의 사전 승인·결과 수신·감사를 Unit lifecycle로 제어하는 범용 Catalog entry 계약
- 문서 역할: 폐기된 설계 기록. 구현 기준은 [Agent Control Engagement](engagement.md)다.

> **폐기 결정(2026-08-11):** 이 문서 아래의 “Agent Control이 AI-DLC Unit lifecycle을 재사용한다”는 전제는 잘못됐다. Unit은 AI-DLC의 개발 단위이며 Agent Control이 참조·확장·재사용하지 않는다. 아래 내용은 구현 계약으로 사용하지 않는다.

## 개념

Agent Control Plane은 ISEKAI 외부에서 독립적으로 동작하는 에이전트에 로컬 거버넌스를 부여한다. 에이전트의 도메인 로직이나 스킬은 에이전트가 소유하고, ISEKAI는 사전 승인과 사후 검증만 담당한다.

별도 Session 개념을 만들지 않는다. 외부 에이전트의 실행 단위는 **Unit**이며, AI-DLC와 동일한 lifecycle 상태 기계·Decision·Envelope·Evidence·Checkpoint·Amendment를 사용한다. 차이는 각 stage에서 허용하는 action과 제어 시점뿐이다.

### 제어 모델

ISEKAI는 에이전트의 실행 경로에 끼어들지 않는다. 제어는 두 시점에서 일어난다.

1. **사전 승인**: Unit Inception에서 scope·budget·격리 경계를 사람이 승인한다. 에이전트는 승인된 범위 안에서 자율 실행한다.
2. **사후 검증**: 에이전트의 실행 결과를 호스트 에이전트가 MCP를 통해 Core에 전달하면 Core가 scope 검증·Evidence 결박·감사 추적한다.

### 결과 유입 경로

외부 에이전트는 ISEKAI를 직접 호출하지 않는다. MCP는 기존과 동일하게 호스트 에이전트(Codex/Claude/Kiro)의 쓰기 제어용 gateway이며, 외부 시스템 대상 API로 확장하지 않는다.

```text
외부 에이전트 → (자체 환경에서 자율 실행, 결과 산출)
                    ↓
운영자 → "이 결과를 등록해줘" → 호스트 에이전트 (Codex/Claude/Kiro)
                                      │
                                      ▼
                                기존 MCP gateway → Core
                                (역할 변경 없음)
```

호스트 에이전트가 `result-submit`을 호출하는 것이므로 MCP의 기존 보안 경계(호스트 에이전트 → Core 간 쓰기 제어)가 그대로 적용된다.

### 진입점

Agent Control은 별도 intake나 진입점이 없다. 기존 `unit-init`에 `catalog_entry`와 `agent_id`를 명시해서 호출한다.

```text
AI-DLC:        채팅 → intake(자동 분류) → Unit이면 → unit-init
Agent Control: 운영자 지시 → unit-init(catalog_entry="agent-control", agent_id="vuln-scanner-01")
```

intake의 자동 분류(Query / Quick Change / Unit)는 AI-DLC 전용이다. Agent Control은 운영자가 "이 에이전트로 진단 시작"처럼 명시적으로 지시하는 것이므로 자동 라우팅이 필요 없다. `catalog_entry`를 생략하면 기존대로 AI-DLC Unit이 생성된다.

`unit-init` 호출 시 Core가 검증하는 항목:

- `catalog_entry`가 설치된 Catalog에 있고 `active` 또는 `preview`인가
- `agent_id`가 `project.json`의 `agents`에 선언되어 있는가
- 에이전트의 `maximum_action_level`이 Project의 `maximum_agent_level` 이하인가
- `binding_mode`에 따라 동시 바인딩 가능한가

### lifecycle 흐름

```text
운영자 → unit-init(catalog_entry, agent_id, scope, isolation) → Inception
사람   → decision(inception, approved)                         → Construction (에이전트 자율 실행)
                                                                     ↓
운영자 → "결과 등록" → 호스트 에이전트 → result-submit          → Core 검증·Evidence 결박
                                        → result-submit          → 반복
                                                                     ↓
         → transition(validation)       → Validation (결과 종합 검증)
         → transition(releasing)        → Release (보고서·산출물 인도)
         → transition(operating)        → Operations (후속 조치·재진단)
         → transition(learned)          → Learn (다음 engagement에 반영)
```

## AI-DLC와의 공통점과 차이

### 공통: Unit lifecycle 재사용

| 인프라 | 사용 방식 |
|---|---|
| Lifecycle 상태 기계 | Inception → Construction → Validation → Release → Operations → Learn |
| Human Decision gate | Inception 승인, Release 승인, scope 변경 승인 |
| Execution Envelope | 시간 창·iteration 예산으로 에이전트 실행 범위 제한 |
| Evidence | 에이전트 결과를 Evidence로 결박 |
| Checkpoint | 실행 중간 상태 저장·재개 |
| Authorization 원장 | 결과 수신·검증 이력 기록 |
| Amendment | scope 변경 요청·재승인 |
| Project Knowledge | engagement에서 발견한 공통 지식 승격 |

### 차이: Stage별 허용 action

| Stage | AI-DLC | Agent Control |
|---|---|---|
| Inception | Intent·Scope·Requirements 정리 | Agent 선택·Engagement scope·격리 경계 정의 |
| Construction | `managed-edit`·`artifact-write` (Core가 직접 파일 변경) | `result-submit` (에이전트가 자율 실행, 결과를 Core에 보고) |
| Validation | `prove` (Core sandbox에서 테스트 실행) | `result-verify` (제출된 결과의 scope·무결성 검증) |
| Release | 배포 승인 | 보고서·산출물 인도 |
| Operations | 운영 피드백 | 후속 조치·재진단 (retest) |
| Learn | 다음 Unit에 반영 | 다음 engagement에 반영 |

Catalog entry manifest가 stage별 허용 action 목록을 선언하고, Core는 현재 Unit의 Catalog entry에 따라 허용 action을 판정한다. 상태 기계 자체는 동일하다.

## 복수 Active Unit 바인딩

AI-DLC는 한 번에 하나의 Unit만 active binding한다. Agent Control은 여러 에이전트가 동시에 실행될 수 있으므로 **복수 active binding**을 지원한다.

### 바인딩 모델

```json
{
  "active_unit_bindings": [
    {
      "unit": "units/engagement-001",
      "catalog_entry": "ai-dlc",
      "bound_at": "2026-08-10T08:00:00Z"
    },
    {
      "unit": "units/vuln-scan-subnet-a",
      "catalog_entry": "agent-control",
      "bound_at": "2026-08-10T09:00:00Z"
    },
    {
      "unit": "units/soc-alert-triage",
      "catalog_entry": "agent-control",
      "bound_at": "2026-08-10T09:30:00Z"
    }
  ]
}
```

### 규칙

- AI-DLC Unit은 최대 1개만 바인딩할 수 있다. 기존 동작을 변경하지 않는다.
- Agent Control Unit은 여러 개 동시 바인딩할 수 있다.
- 각 Unit의 Envelope·scope·격리 경계는 독립적이다. Unit 간 격리 경계가 겹치면 바인딩을 거부한다.
- `status`·`resume`·`verify`는 Unit ID를 명시해야 한다. 바인딩이 복수이면 Unit 생략 시 목록을 반환한다.
- Catalog entry manifest에 `"binding_mode": "multiple"` 또는 `"single"`을 선언한다. 기본값은 `"single"`이다.

### Core 변경 범위

| 현재 | 변경 후 |
|---|---|
| `active_unit_binding`: 단일 객체 또는 null | `active_unit_bindings`: 배열 |
| `require_active_unit_match`: 단일 비교 | 배열에서 매칭 |
| `bind_active_unit`: 기존 바인딩 있으면 거부 | Catalog entry의 `binding_mode`에 따라 판정 |
| `intake` 라우팅: active binding 유무로 분기 | AI-DLC binding 유무로 분기 (Agent Control binding은 라우팅에 영향 없음) |

## Agent 선언

외부 에이전트는 `project.json`의 `agents` 배열에 선언한다. 프로젝트 설정이며 MCP action이 아니다.

```json
{
  "id": "isekai-project",
  "kind": "project",
  "schema_version": "1.0.0",
  "version": "0.3.0",
  "maximum_agent_level": "L2",
  "agents": [
    {
      "agent_id": "vuln-scanner-01",
      "kind": "external-agent",
      "capabilities": ["network-scan", "exploit-verify", "report-generate"],
      "maximum_action_level": "L2",
      "credential_boundary": "secret://vault/vuln-scanner"
    },
    {
      "agent_id": "soc-responder-01",
      "kind": "external-agent",
      "capabilities": ["alert-triage", "log-analysis", "containment"],
      "maximum_action_level": "L1",
      "credential_boundary": "secret://vault/soc-readonly"
    }
  ]
}
```

- `maximum_action_level`은 Project의 `maximum_agent_level`을 초과할 수 없다.
- `credential_boundary`는 불투명 참조만 기록하며 key 원문을 포함하지 않는다.
- 선언되지 않은 에이전트의 Unit 생성은 거부된다.

## Unit lifecycle stage별 계약

### Inception

Engagement의 목적·scope·agent·격리 경계를 정의한다. AI-DLC의 Inception과 동일한 구조를 따르되 agent 관련 필드를 추가한다.

```json
{
  "intent": {
    "objective": "내부 서브넷 서비스 취약점 진단",
    "agent_id": "vuln-scanner-01",
    "target": ["192.168.1.0/24"],
    "allowed_actions": ["network-scan", "service-enum", "vuln-check"],
    "prohibited_actions": ["exploit-execute", "credential-extract", "lateral-movement"],
    "action_level": "L2"
  },
  "isolation": {
    "data_boundary": ["engagement-001"],
    "network_scope": ["192.168.1.0/24"],
    "credential_scope": ["secret://vault/scanner-readonly"],
    "filesystem_scope": ["/workspace/engagement-001/**"],
    "prohibited": ["production-write", "credential-extract"]
  }
}
```

사람이 Inception Decision을 승인하면 Execution Envelope이 생성되고 Construction으로 전이한다.

### Construction

에이전트가 승인된 scope 안에서 자율 실행하는 단계다. AI-DLC에서는 Core가 `managed-edit`·`artifact-write`로 직접 파일을 변경하지만, Agent Control에서는 에이전트가 외부에서 실행하고 `result-submit`으로 결과를 보고한다.

```json
{
  "submission_id": "sub-001",
  "submitted_at": "2026-08-10T10:30:00Z",
  "kind": "finding",
  "payload": {
    "title": "SSH 서비스 취약한 키 교환 알고리즘",
    "target": "192.168.1.15:22",
    "severity": "medium",
    "evidence_ref": "raw/scan-output-001.json"
  },
  "payload_digest": "sha256:abc123..."
}
```

Core가 `result-submit` 수신 시 검증하는 항목:

| 검증 | 실패 시 |
|---|---|
| Unit이 `construction` 또는 `validation` 상태인가 | 거부 |
| Envelope이 유효한가 (시간·iteration) | Envelope 갱신 요구 |
| target이 승인된 scope 안에 있는가 | Unit 중단, 사람 알림 |
| action이 prohibited에 해당하는가 | Unit 중단, 사람 알림 |
| payload digest가 일치하는가 | 거부 |

검증을 통과한 결과는 Evidence로 결박되고 authorization 원장에 기록된다.

### Validation

제출된 결과의 종합 검증 단계다. `result-verify`로 전체 결과의 scope 일관성·완전성·Evidence 결박을 확인한다.

### Release

보고서·산출물을 인도하는 단계다. Release Decision에서 Evidence ID와 digest를 결박한다.

### Operations

후속 조치·재진단(retest) 단계다. 새 결과가 필요하면 Amendment를 기록하고 재승인한다.

### Learn

engagement에서 발견한 공통 지식을 Project Knowledge로 승격할 수 있다.

## Action 수준

| 수준 | 허용 범위 | 예시 |
|---|---|---|
| L0 | 읽기 전용 관측 | 로그 수집, 설정 조회, 상태 모니터링 |
| L1 | 로컬 분석·보고 | 정적 분석, 보고서 생성, 로컬 테스트 |
| L2 | 제한된 외부 상호작용 | 네트워크 스캔, 제한된 API 호출, sandbox 실행 |
| L3 | 능동 실행 | 익스플로잇 검증, 설정 변경, 프로세스 제어 |

L3는 AI-DLC에 없는 수준이다. L3 Unit은 Inception Decision에서 별도 Foundation 정책과 추가 사람 확인을 요구한다.

## 격리 경계

격리 경계는 Unit Inception에서 고정된다.

- 격리 경계를 확장하려면 기존 Unit을 종료하고 새 Unit을 열어야 한다. Amendment로 축소는 가능하다.
- 결과 검증에서 격리 경계 밖의 target이 감지되면 Unit을 중단하고 사람에게 판단을 요청한다.
- 격리 경계의 실행 시점 강제는 에이전트 호스트 환경의 책임이다. Core는 사후 검증으로 일탈을 감지한다.
- 동시 바인딩된 Unit 간 격리 경계가 겹치면 바인딩을 거부한다.

## 감사 추적

모든 결과 수신·검증·Decision·Checkpoint는 Unit의 authorization 원장과 Evidence에 기록된다. AI-DLC와 동일한 감사 구조를 사용하므로 별도 감사 계약이 필요 없다.

Unit 종료(learned 또는 abandoned) 시 전체 원장의 digest를 고정한다.

## 도메인 확장

범용 계약 위에 도메인별 차이는 Profile로 분리한다.

| 도메인 | Profile이 추가하는 것 |
|---|---|
| 취약점 진단 | Engagement scope, Finding 타입, Retest 정책 |
| SOC | Alert 분류, Playbook 참조, Escalation 규칙 |
| 데이터 파이프라인 | 데이터 분류, 접근 정책, 품질 기준 |
| 코드 생성 | 변경 범위, 테스트 요구, 배포 정책 |

Profile은 Foundation에서 관리하며 Agent Control Plane의 core 계약을 변경하지 않는다.

## Trust boundary

Agent Control Plane은 다음을 **검증하지 않는다**:

- 에이전트가 승인된 scope 안에서만 실행했는지 (실행 시점 강제는 에이전트 호스트 환경의 책임)
- 에이전트가 모든 결과를 빠짐없이 보고했는지 (운영자가 호스트 에이전트에 전달하지 않은 결과는 감지 불가)
- 에이전트의 capability 선언이 실제와 일치하는지
- 호스트 에이전트가 외부 에이전트 결과를 정확히 중계했는지 (호스트 에이전트의 중계 충실성은 AI-DLC에서 Agent가 사용자 메시지를 보고하지 않을 수 있는 것과 같은 성격)

결과 유입 경로가 "외부 에이전트 → 운영자 → 호스트 에이전트 → MCP → Core"이므로, Core가 직접 검증할 수 있는 것은 MCP에 도착한 시점 이후의 scope 일치·digest 무결성뿐이다. 그 앞 구간의 무결성은 에이전트 호스트 환경의 실행 격리, 네트워크 정책, credential 관리가 보완한다.

## 권한 불변식

- Agent Control Plane은 Foundation, Project Agent level, Unit Envelope, Human Gate를 확장하지 못한다.
- 격리 경계는 Inception에서 고정되며 확장하려면 새 Unit이 필요하다.
- L3 Unit은 별도 Foundation 정책과 추가 사람 확인을 요구한다.
- Credential 원문은 계약에 기록하지 않는다.
- scope 일탈이 감지되면 Unit을 즉시 중단하고 사람 승인 없이 재개하지 않는다.
- 동시 바인딩된 Unit 간 격리 경계가 겹치면 안 된다.

## MCP action 목록

에이전트 선언은 `project.json` 설정이며 MCP action이 아니다. Unit lifecycle action(`unit-init`, `decision`, `transition`, `envelope-propose`, `envelope-approve`, `checkpoint`, `verify`, `status`, `resume`, `amend`, `evidence`)은 AI-DLC와 동일하게 Core가 제공한다.

Agent Control 전용 action:

| Action | 설명 | Stage |
|---|---|---|
| `result-submit` | 에이전트 실행 결과 제출·scope 검증·Evidence 결박 | Construction, Validation |
| `result-verify` | 전체 결과의 scope 일관성·완전성 검증 | Validation |
| `isolation-verify` | 격리 경계 위반 여부 검증 | 모든 stage |
