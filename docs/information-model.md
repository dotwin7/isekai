# 범용 Data·Semantic·Knowledge Model

- 설명: 도메인 중립 정보 구조, 계층 책임, 공통 메타데이터 계약과 Persistent Context
- 문서 역할: [ISEKAI canonical 문서 집합](isekai.md)의 일부

정보 구조는 특정 도메인 객체를 Foundation Core에 고정하지 않는다. 범용 계약을 먼저 정의하고 도메인과 제품이 확장한다.

```text
Domain-neutral Core
        ↓
Domain Profile (Security, Software Delivery, future domains)
        ↓
Product Extension (reference product, assessment service, future products)
```

## 계층과 책임

| 계층 | 책임 | 책임이 아닌 것 |
|---|---|---|
| Operational Data | 현재 사실과 인스턴스의 원장 | 공통 의미·추론 규칙 결정 |
| Semantic Layer | 원천 필드·값·지표를 공통 의미로 매핑·노출 | 원천 사실 복제·소유 |
| Ontology | 개념 유형·허용 관계·도메인 제약 정의 | 현재 사실·권한의 원장 |
| Knowledge Layer | 검토된 설명·절차·판단 근거·경험 제공 | 실행 허용 여부 결정 |
| Policy Layer | 규범적 규칙·승인·Scope와 실행 허용 판단 | 일반 참고 지식 저장 |
| Evaluation Layer | 독립 입력·기대 결과·품질 기준 관리 | 운영 Agent에 golden label 제공 |

## Domain-neutral Core

Core는 도메인별 이름 대신 재사용 가능한 최소 추상 계약만 제공한다.

| Core 개념 | 의미 |
|---|---|
| Entity | 식별 가능한 대상 |
| Relation | Entity 사이의 형식화된 관계 |
| Observation | 특정 시점에 관측된 사실 |
| Claim | 검토·판단이 필요한 주장 |
| Evidence | Claim·Decision을 뒷받침하는 근거 |
| Decision | 선택과 그 이유를 기록한 결과 |
| Action | 수행됐거나 제안된 행위 |
| Scope | 데이터·행위·시간의 적용 경계 |

## Domain Profile과 Product Extension

Domain Profile은 Core를 전문화하는 용어·관계·제약·Semantic mapping 묶음이다. Product Extension은 해당 Profile을 변경하지 않고 제품 전용 필드·워크플로·지표를 namespace 아래 추가한다.

```text
Security Profile: Asset, Identity, Event, Alert, Case, Finding, Control
Software Delivery Profile: Requirement, Component, Change, Build, Release
Reference Product Extension: product:* 객체·관계·지표
```

OCSF는 Security Profile의 이벤트 mapping 출발점이며 범용 Core 자체가 아니다. 다른 표준과 도메인은 별도 Profile·Adapter로 연결한다.

## 공통 메타데이터 계약

모든 Profile과 Extension은 최소한 다음 메타데이터를 유지한다.

```yaml
id: stable-id
type: profile-qualified-type
schema_version: 1.0.0
owner: accountable-owner
status: draft | approved | deprecated
provenance: source-reference
observed_at: optional-timestamp
effective_from: optional-timestamp
expires_at: optional-timestamp
confidence: optional-score
classification: data-classification
scope: tenant-workspace-project
```

## 신뢰·원장 경계

- Semantic mapping은 원본 값·출처·변환 버전과 lineage를 보존한다.
- Ontology의 관계는 가능한 구조를 정의하며 실제 관계 인스턴스는 권위 있는 원천에서 온다.
- Knowledge는 후보→리뷰→승인→폐기 수명주기를 가지며 유형별 책임자가 승인한다.
- Policy는 Knowledge 검색 결과가 아니라 승인 원장과 Policy Engine에서 집행한다.
- Evaluation의 기대 결과는 운영 컨텍스트와 격리해 평가 오염을 막는다.
- Domain Profile은 Core의 필수 메타데이터·Scope·출처 계약을 완화할 수 없다.
- Git은 스키마·정책 정의의 버전 원장일 수 있지만 운영 사실·고객 Evidence·비밀정보의 원장은 아니다.
- Obsidian은 연구·초안 도구일 수 있지만 공식 운영 원장은 아니다.
- 중앙 Registry·Knowledge Service는 여러 제품의 공동 배포 필요가 확인된 뒤 도입한다.

## Persistent Context

규칙과 지식을 모델의 기억에만 의존하지 않는다.

```text
Project Artifacts + Foundation Version + Decisions
+ Context Receipt + Checkpoint + Evidence References
= 재구성 가능한 작업 컨텍스트
```

- **Context Receipt:** Unit, Foundation 버전, 적용 규칙, Knowledge·Semantic 참조, Agent 권한과 Scope
- **Project Knowledge release:** Unit에서 나온 재사용 항목을 사람의 Knowledge Decision으로 승인한 프로젝트 범위 snapshot. 새 Unit은 생성 시점 release digest와 자기 `work_scope`에 겹치는 활성 항목만 Receipt에 고정하며 기존 Unit은 자동 갱신하지 않는다.
- **Checkpoint:** 완료·미완료 단계, 결정과 근거, 차단 요소와 다음 행동
- **복구:** 컨텍스트 압축·세션 종료·에이전트 교체 후 원본 산출물과 Checkpoint에서 재개

전체 Foundation과 Knowledge를 상시 프롬프트에 넣지 않는다. 오래된 대화보다 원본 Decision과 Evidence를 우선한다. Scope·승인·정책을 복구하지 못하면 고위험 실행은 중단한다.
