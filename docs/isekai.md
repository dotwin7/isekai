# ISEKAI

- 상태: Canonical
- 작성일: 2026-08-03
- 설명: 범용 AI-DLC 운영 모델 및 Security Engineering Domain Profile
- 문서 역할: canonical 설계 문서 집합의 입구. 세부 계약은 아래 문서 맵의 파일이 주제별로 소유하며, 이 문서와 문서 맵의 파일 전체가 이 저장소의 유일한 canonical 설계 문서 집합이다.

## 1. 정의

> ISEKAI는 기존 AI 에이전트를 활용해 소프트웨어 제품과 서비스를 AI 중심으로 기획·개발·검증·운영하는 범용 AI-DLC와, 이를 도메인별로 일관되게 적용하는 Engineering Foundation이다. 보안은 첫 번째 Domain Profile이자 우선 적용 영역이다.

최상위 목표는 Agent Platform을 만드는 것이 아니라 **AWS AI-Driven Development Life Cycle과 유사한 범용 AI 중심 개발 생명주기를 구현하는 것**이다. 보안기술팀과 보안 제품에서 먼저 검증하되 Core의 workflow·information model·governance는 특정 도메인에 종속시키지 않는다. CLI, Registry, Context Service와 Control Plane은 이 목표에 필요한 만큼 단계적으로 만든다.

참고: [AWS, AI-Driven Development Life Cycle: Reimagining Software Engineering, 2025-07-31](https://aws.amazon.com/blogs/devops/ai-driven-development-life-cycle/)

## 2. 목표

1. **범용 제품 개발:** 도메인과 기술 스택에 관계없이 현재 및 향후 제품을 동일한 AI-DLC Core로 지속 개발한다.
2. **보안운영:** 사람과 에이전트가 탐지·조사·판단·대응 준비를 함께 수행한다.
3. **보안 제품 개발:** Security Domain Profile을 사용해 보안 제품을 공통 Foundation 위에서 개발한다.
4. **보안서비스:** 취약점 진단과 승인된 레드팀 서비스에 에이전트를 결합한다.

AI-DLC Core와 Foundation은 특정 제품이나 도메인에 종속되지 않으며 서로 다른 제품과 서비스가 공통으로 사용한다. Security Profile과 보안 정책은 범용 Core 위에 선택적으로 적용한다. 초기 적용 대상은 Foundation v0.1 이후 별도 Unit으로 선정한다.

## 3. 핵심 원칙

1. **AI 중심, 인간 책임:** AI가 계획·질문·구현을 주도하고 사람은 중요한 결정을 책임진다.
2. **Foundation 우선:** 공통 규칙 v0.1을 확정한 뒤 제품 Unit에 적용한다.
3. **범용 Core:** 공통 정보 모델과 거버넌스는 도메인에 중립적이며, 보안 개념은 Domain Profile로, 제품 차이는 Product Extension으로 분리한다.
4. **기존 에이전트 활용:** Claude, Codex, Kiro 등 검증된 에이전트를 실행 엔진으로 사용한다.
5. **자체 Brain 없음:** 범용 Agent Loop·Planner·Reasoner를 새로 만들지 않는다.
6. **필요한 컨텍스트만:** 전체 규칙과 지식을 상시 프롬프트에 넣지 않는다.
7. **외부 경계 통제:** 고위험 부작용은 AI의 기억이 아니라 권한·승인·원격 경계에서 통제한다.
8. **증거와 재현성:** Decision, Evidence, 평가와 운영 결과를 다음 작업의 컨텍스트로 보존한다.
9. **점진적 플랫폼화:** 여러 제품·사용자의 실제 수요가 확인될 때 공유 서비스를 분리한다.
10. **모델 능력 보존:** 출력 압축·재작성과 과도한 규율로 모델의 추론을 방해하지 않는다.

## 4. 무엇을 만들 것인가

ISEKAI는 다섯 가지로 구성된다.

| 구성 | 역할 |
|---|---|
| AI-DLC Workflow | Inception부터 Operations까지의 상태·산출물·인간 게이트 |
| Engineering Foundation | 범용 Core, Domain Profile과 개발·운영·Agent·Policy·평가 공통 규칙 |
| Persistent Context | Unit, Decision, Evidence, Receipt, Checkpoint |
| Agent Integration | 기존 에이전트를 교체 가능한 실행 엔진으로 연결 |
| Security Extensions | 기준 제품, 신규 제품, 보안운영, 진단·레드팀 확장 |

```text
                         ISEKAI
                            │
              AI-DLC Workflow + Foundation
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
   기준 제품           향후 보안 제품       보안서비스
  제품·도메인 기능       제품별 Extension    진단·레드팀
        │                   │                   │
        └───────────────────┼───────────────────┘
                기존 Agent + Persistent Context
```

Agent Integration은 사용자가 별도의 Python 모듈 명령을 실행하는 구조가 아니다. 사용자는 Kiro와 Claude Code에서 `/isekai`, Codex에서 `$isekai`를 호출하고 Runtime Adapter가 Project-local `isekai` launcher를 내부적으로 호출한다. Marketplace에서 Plugin을 별도로 설치한 Claude·Codex 세션은 namespaced alias도 제공한다. launcher는 같은 로컬 환경의 ISEKAI Core dispatch를 실행하고, Core는 프로젝트가 선택한 Foundation·Profile·Extension과 Unit artifact를 읽고 검증한다. 직접 CLI를 사용하는 경우에는 `isekai <action>`을 사용하며, `isekai plugin <action>`은 Runtime Adapter의 내부 호환 계약이다.

```text
User / Agent Host
        ↓ /isekai
Runtime Plugin Adapter
        ↓ isekai plugin <action>
Local ISEKAI Core
        ↓
Foundation + Project + Unit artifacts
```

Core는 기본적으로 서버가 아니며, Plugin은 Host 연결과 사용자 명령 표면을 담당하고 Core는 workflow·Decision·Evidence·authorization을 담당한다.

## 5. 문서 맵

| 문서 | 내용 |
|---|---|
| [architecture.md](architecture.md) | Core 내부 모듈 경계, Project bootstrap과 discovery |
| [installation.md](installation.md) | Git release 설치, 프로젝트 버전 고정, update·rollback |
| [workflow.md](workflow.md) | Query·Quick Change·Unit 작업 라우팅과 AWS형 AI-DLC 수명주기 |
| [unit.md](unit.md) | Unit 구조, 인간 게이트, Decision·Evidence·Execution Envelope, 원장 동시성 |
| [project-knowledge.md](project-knowledge.md) | Unit 학습의 후보·승인·승격과 후속 Unit별 버전 고정 |
| [foundation.md](foundation.md) | Engineering Foundation 계층, Security Profile, v0.1 완료 조건과 승인 절차 |
| [information-model.md](information-model.md) | 범용 Data·Semantic·Knowledge Model과 Persistent Context |
| [agent-integration.md](agent-integration.md) | Runtime Adapter, 세션 모드, 실행 통제 |
| [live-smoke.md](live-smoke.md) | 실제 Runtime Skill 발견, 활성화, intake와 Golden Path 검증 |
| [roadmap.md](roadmap.md) | 제품·서비스 적용, 단계적 구현, 백로그, 성공 기준, 책임, 지표, 비목표, 남은 결정 |
