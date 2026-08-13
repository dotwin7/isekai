# Agent Control Engagement

- 상태: `agent-control@0.1.0` preview 구현 계약
- 설명: 외부 에이전트 실행을 승인·추적·감사하는 Agent Control Catalog entry
- 문서 역할: [ISEKAI canonical 문서 집합](../isekai.md)의 일부

## 소유권 경계

`Unit`은 AI-DLC가 소유하는 개발 작업 단위다. Agent Control은 Unit lifecycle, Unit artifact, AI-DLC action 또는 `isekai.catalog.ai_dlc` 구현을 import·수정·재사용하지 않는다.

| 소유자 | 책임 |
|---|---|
| ISEKAI Core | Catalog 발견·action dispatch, Project 고정, 공통 파일 안전성, Project Knowledge 원장 |
| AI-DLC Catalog | Unit, 개발 lifecycle, 개발 Decision·Envelope·Evidence, edit·prove |
| Agent Control Catalog | Engagement, 승인, Connector Execution 원장, Result Receipt |
| Connector | 외부 플랫폼 프로토콜 변환과 결과 정규화 |
| Nahonza | 실제 분석, worker orchestration, harness/tool policy, 단기 memory |

Core가 제공하는 공통 기반을 함께 사용할 수는 있지만 한 Catalog가 다른 Catalog의 내부 구현을 경유해서는 안 된다. 구조 테스트는 `isekai.catalog.agent_control`에서 `isekai.catalog.ai_dlc` import를 금지한다.

## 도메인 모델

```text
Engagement
├─ 승인된 목적·connector·operation·scope·실행 예산
├─ 고정된 Project Knowledge context
└─ Connector Execution 1..N
   ├─ 실행별 authorization/request digest
   ├─ remote task ID와 단조 상태
   └─ terminal Result Receipt
```

Engagement는 하나의 취약점 진단이나 사고 조사처럼 목적·책임·최종 결과가 같은 업무 묶음이다. 같은 범위의 심화 분석, 오탐 확인과 재진단은 각각 새 Connector Execution으로 추가한다.

다른 고객·tenant·목적·connector·operation 또는 승인 scope가 필요하면 새 Engagement를 만든다. 첫 구현은 한 Engagement에 하나의 in-flight Execution만 허용한다.

## 실행 흐름

```text
운영자
  → Engagement 생성(proposed)
  → 사람 승인(active)
  → Execution 시작(dispatching)
  → Nahonza POST /api/v1/agent/execute
  → 202 + taskId
  → status polling(queued/running)
  → completed/failed
  → Result Receipt와 digest 저장
  → 필요하면 같은 Engagement에 후속 Execution
```

ISEKAI는 Nahonza 결과를 다시 LLM 분석하지 않는다. 승인 범위, task identity, 상태 전이와 결과 digest를 검증하고 보존할 뿐이다. 결과 품질 판단은 사람 검토나 별도로 승인된 Nahonza review Execution이다.

## Memory Hub 결합

Nahonza memory와 ISEKAI Project Knowledge는 역할이 다르다.

| 저장소 | 역할 | 신뢰·수명 |
|---|---|---|
| Nahonza local/distributed memory | 실행 중 agent 작업 기억 | TTL, Redis 장애 시 no-op 가능 |
| Nahonza knowledge base | 제품이 배포하는 보안 기준 지식 | Nahonza release 소유 |
| ISEKAI Project Knowledge | 사람이 승인한 프로젝트 공통 사실 | digest chain을 가진 장기 원장 |

Engagement 생성 시 사용자가 현재 승인 release의 entry ID를 명시적으로 선택한다. Agent Control은 선택한 `id`, `kind`, `title`, `statement`와 release/context digest만 Engagement에 고정한다. Connector는 이 고정본을 Nahonza 입력에 전달하되 tool 권한이나 scope로 해석하지 않는다.

Nahonza 결과와 memory는 Project Knowledge로 자동 승격하지 않는다. 결과의 지식 제안을 장기 기억으로 넣으려면 향후 Unit에 종속되지 않는 Core Knowledge Source 계약과 별도 사람 승인이 필요하다.

## Nahonza connector

Project는 connector instance를 다음처럼 선언한다.

```json
{
  "agent_control": {
    "connectors": [
      {
        "id": "nahonza-offsec",
        "kind": "nahonza",
        "transport": "agent-api",
        "endpoint_ref": "env://NAHONZA_OFFSEC_URL",
        "auth_ref": "env://NAHONZA_OFFSEC_TOKEN",
        "allowed_operations": ["va", "pentest", "redteam", "verify"],
        "maximum_action_level": "L2"
      }
    ]
  }
}
```

URL과 credential 원문은 Project나 Engagement에 기록하지 않는다. connector가 호출 시점에 `env://` 참조를 해석한다. 해석된 endpoint는 사용자 정보·query·fragment가 없는 외부 DNS 이름의 HTTPS 443 URL이어야 한다. 호출 직전에 DNS의 모든 응답이 global 주소인지 확인하고, 검증한 주소 하나에 연결을 고정한 채 원래 hostname으로 TLS를 검증한다. redirect는 따르지 않는다.

connector의 전체 선언과 `maximum_action_level`은 승인된 Engagement에 함께 결박된다. 실행 시작과 polling 직전에 현재 Project의 connector 선언이 승인본과 같은지, 현재 Project `maximum_agent_level`이 승인 action level 이상인지 다시 검사한다.

현재 Nahonza 계약에 맞춰 non-SSE `202 + taskId`와 `GET /status/{taskId}`를 authoritative 경로로 사용한다. SSE는 진행 표시용 보조 경로이며 v0.1에는 사용하지 않는다.

Nahonza가 POST 멱등성을 보장하지 않으므로 connector는 자동 POST 재시도를 하지 않는다. task ID를 받기 전 연결이 끊기면 Execution을 `uncertain`으로 기록하고 사람 확인 전 재실행하지 않는다. Nahonza의 status store가 프로세스 메모리와 제한된 retention을 사용하므로 terminal 결과는 즉시 ISEKAI Result Receipt로 고정해야 한다.

## 저장 구조

```text
engagements/ENG-.../
├─ engagement.json
├─ approval.json
├─ executions.json
└─ results/EXEC-....json
```

승인은 목적·connector 전체 선언·operation·scope·실행 예산·Knowledge context digest를 결박한다. 각 Execution은 승인 digest, request digest, remote task ID와 result digest를 기록하고 이전 Execution digest에 연결된다. 완료 Result Receipt는 결과 전체의 digest와 receipt 전체의 digest를 Execution에 역결박하며, 상태 조회·후속 실행·polling 때 원장 chain과 모든 완료 receipt를 다시 검증한다. 생성·승인·완료 저장은 staging 또는 rollback을 사용해 부분 상태가 정상 계약으로 노출되지 않게 한다.

## Runtime action

| Action | 책임 |
|---|---|
| `agent-engagement-create` | proposed Engagement와 빈 execution ledger 생성 |
| `agent-engagement-approve` | 사람이 승인한 계약 digest 고정 |
| `agent-engagement-status` | Engagement·승인·Execution 상태 조회 |
| `agent-execution-start` | 승인 범위와 예산 검사 후 Nahonza task 시작 |
| `agent-execution-status` | task polling과 terminal Result Receipt 저장 |

entry가 `preview`인 동안 이 handler는 Core Runtime/CLI/MCP 공개 action registry에 등록하지 않으며 직접 호출해도 Catalog 상태 검사에서 fail-closed한다. 서비스와 connector 구현·테스트가 존재해도 실제 활성화는 live Nahonza smoke, restart/retention 실패 시나리오와 운영 승인 후 manifest를 `active`로 바꾸고 handler를 공개 registry에 결합하는 별도 변경이다.

## 활성화 조건

- Agent Control package에 AI-DLC import가 없다.
- Engagement와 Unit 경로·원장·action이 겹치지 않는다.
- Project 고정, symlink, concurrent start와 scope subset 테스트가 통과한다.
- POST timeout은 `uncertain`이며 자동 재시도하지 않는다.
- polling 상태는 뒤로 이동하지 않는다.
- terminal result의 크기 제한과 digest receipt가 검증된다.
- Project Knowledge는 선택된 승인 항목만 전달된다.
- 실제 Nahonza 인증·execute·status smoke가 통과한다.
