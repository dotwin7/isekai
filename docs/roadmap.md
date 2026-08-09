# Roadmap과 거버넌스

- 설명: 제품·서비스 적용, 단계적 구현, 백로그, 성공 기준, 책임, 지표, 비목표와 남은 결정
- 문서 역할: [ISEKAI canonical 문서 집합](isekai.md)의 일부

## 제품·서비스 적용

아래 보안 적용은 범용 AI-DLC Core를 검증하는 첫 사례다. 비보안 제품은 Software Delivery Profile 또는 별도 Domain Profile과 Product Extension을 선택하며 동일한 Workflow·Decision·Evidence 계약을 사용한다.

### 기준 제품

Foundation v0.1을 처음 검증할 실제 제품은 별도 Unit에서 선정한다. 선정된 제품의 기능과 Agent 협업을 AI-DLC로 개발하고, 검증된 공통 규칙·Semantic·Knowledge만 Foundation Core로 승격한다.

### 향후 제품

Foundation 버전과 Product Profile을 선택하고 제품별 Semantic·Knowledge·Evaluation Extension을 가진다. 공통 Core 의미는 변경하지 않는다.

### 취약점 진단·레드팀

Engagement, Scope, Finding, Evidence, Report와 Retest 계약을 사용한다. 고객별 데이터·자격증명·실행 환경을 격리하고 능동적 실행은 별도 인간 승인을 요구한다.

## 단계적 구현

### Stage 0: Foundation v0.1

Constitution, 규칙, Profile, Exception, Evaluation과 DoD를 정의하고 책임자를 확정한다.

### Stage 1: Local AI-DLC MVP

- 프로젝트 등록과 Foundation 버전 선언
- Query·Quick Change·Unit 라우팅
- Unit 생성·상태 전환·인간 Decision
- AI-DLC 산출물 scaffold
- 한 개 Agent Adapter
- Context Receipt·Checkpoint
- 평가 Evidence 연결과 상태 조회

### Stage 2: Reference Product Implementation

선정된 기준 제품의 실제 Unit에 AI-DLC를 적용해 Foundation 규칙, Agent 협업, Semantic·Knowledge 변경과 평가를 검증한다.

첫 결정론적 Reference Product E2E는 `FeatureProposal` 우선순위 기능을 사용한다. 테스트는 빈 임시 프로젝트에 Codex Plugin과 Core를 설치하고, 프로젝트 초기화·adaptive intake·Level-1 plan·Unit·Execution Envelope·실제 제품 코드/테스트·Evidence·Decision·Checkpoint를 거쳐 `learned`까지 검증한다. Release와 Operations가 범위에 없을 때는 `skip` disposition과 이유를 승인 Envelope에 남긴다.

이 E2E는 설치된 launcher와 Runtime action 계약을 통해 Host Agent 행동을 결정론적으로 시뮬레이션한다. 실제 Codex·Claude·Kiro 모델 세션의 Skill 준수와 대화 UX는 Runtime별 live smoke로 별도 검증해야 하므로 Stage 2 전체 완료로 간주하지 않는다.

### Stage 3: Shared Foundation Services

두 개 이상의 제품·팀에서 공동 사용이 발생할 때 필요한 서비스를 분리한다.

- Foundation Release Registry
- Shared Knowledge·Semantic Registry
- Context API와 Evaluation Service
- 조직·제품·Workspace 관리

### Stage 4: Security Agent Control Plane

L2 이상 실행 권한, 고객 데이터, 장시간 세션이나 중앙 승인이 필요할 때 확장한다.

- Agent Session·Identity·Scope
- Approval·Tool Policy
- Audit·Checkpoint
- Credential Broker·격리 실행
- 중앙 중지와 운영 관측

범용 OpenAgent가 아니라 실제 보안업무에 필요한 기능만 구현한다.

## 첫 번째 백로그

1. Foundation Charter와 규칙 구조
2. Domain-neutral Core와 공통 메타데이터 스키마
3. Domain Profile·Product Extension·Adapter 계약
4. Rule·Policy·Evaluation·Exception·DoD 스키마
5. Query·Quick Change·Unit 라우팅 기준
6. Unit 상태와 Human Decision 계약
7. Inception·Construction·Operations 템플릿
8. Project·Foundation manifest와 Context Receipt·Checkpoint
9. 한 개 Agent Adapter와 로컬 조회·검증 인터페이스
10. Foundation v0.1 범용성·적합성 테스트

기준 제품 Unit은 위 항목의 최소 버전이 동작한 뒤 시작한다.

## MVP 성공 기준

- Query가 불필요한 AI-DLC 산출물 없이 처리된다.
- Quick Change가 최소 변경·검증 결과만 남긴다.
- 하나의 Unit이 Intent에서 Operations Feedback까지 추적된다.
- 인간 Decision 없이는 중요한 게이트를 넘지 않는다.
- 새 세션·다른 에이전트가 Checkpoint와 산출물로 작업을 재개한다.
- Foundation 버전·규칙·예외·평가 Evidence를 재현할 수 있다.
- 전체 규칙과 대화 원문을 상시 프롬프트에 넣지 않는다.
- 출력 압축·재작성 없이 기존 에이전트를 사용할 수 있다.
- Security Domain Profile과 독립된 예시 Domain Profile이 Core 변경 없이 동작하고, 서로 다른 제품은 Product Extension만으로 확장된다.
- 고위험 실행은 외부 승인과 권한 경계에서 차단된다.

## 책임

| 책임 | 역할 |
|---|---|
| AI-DLC Owner | 라우팅·생명주기·Unit·게이트·성과 지표 |
| Foundation Owner | 공통 규칙·버전·호환성·예외 |
| Product Owner | 제품 의도·우선순위·인수 조건 |
| Domain Owner | Domain Profile·Ontology·도메인 Knowledge 승인 |
| Data/Semantic Owner | Core 의미·mapping·lineage·지표 품질 |
| Policy Owner | 규범적 규칙·승인·Scope와 집행 기준 |
| Agent Owner | Adapter·capability·실패 분석 |
| Evaluation Owner | 독립 평가셋·기대 결과·품질 기준과 격리 |
| Security Approver | 고위험 권한·운영·고객 실행 승인 |

## 지표

### AI-DLC

- Intent에서 검증된 Release까지의 리드타임
- Query·Quick Change·Unit 라우팅 정확도와 전환율
- AI 질문 후 Human Decision 대기 시간
- Unit 재작업률·결함 유출률·추적 완전성
- 새 세션·에이전트의 재개 성공률

### Foundation

- Domain Profile 간 Core 재사용률과 Core 변경 없이 추가된 Product Extension 비율
- 규칙·Semantic·Ontology·Knowledge·Policy 중복과 충돌
- provenance·Scope·버전 메타데이터 완전성
- 예외 수·만료·보완 통제 준수율
- Foundation 업그레이드 호환성

### Agent 협업

- 제안 수용·수정·거절 비율
- 근거·출처·불확실성 표시율
- 평가 회귀와 사람 재작업률
- Scope·권한 위반 건수

## 비목표

- 범용 OpenAgent·자체 Agent Brain·모델 라우터
- 모든 작업에 정식 AI-DLC 강제
- 상시 장문 규율 프롬프트와 출력 압축
- 모델 추론·출력 스타일 미세관리
- 모든 로컬 도구를 프록시하는 거대한 Gateway
- 초기부터 중앙 Registry·Portal·Control Plane 구축
- 제품 기능 없이 Foundation만 장기간 개발
- 거대한 온톨로지·Knowledge Graph 선행 구축
- 사람 승인 없는 자율 대응·진단·레드팀 실행

## 남은 결정

1. Foundation 후속 버전의 소유자와 승인자
2. Unit Profile별 필수·선택 산출물
3. Foundation 저장소와 제품 저장소 경계
4. 첫 기준 제품 Unit
5. Shared Service 전환 기준
