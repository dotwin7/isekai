# ISEKAI Features

- 설명: AI-DLC와 추가 기능을 동등한 Feature로 결합하는 Runtime 패키지 및 MCP 제어 계약
- 문서 역할: [ISEKAI canonical 문서 집합](isekai.md)의 일부

## 개념 경계

ISEKAI Feature가 이세카이가 제공하는 기능의 공통 단위다. Feature Catalog는 설치된 Runtime에서 사용할 수 있는 모든 Feature를 하나의 검증 가능한 목록으로 묶는다.

```text
ISEKAI Runtime
├─ Core MCP control plane
└─ Feature Catalog
   ├─ Feature: AI-DLC
   └─ Feature: future functions
```

| 개념 | 책임 | 소유 위치 |
|---|---|---|
| Feature Catalog | 설치된 Feature의 ID·버전·상태·action·resource·digest 목록 | ISEKAI Core |
| ISEKAI Feature | Core MCP를 통해 제어되는 버전형 기능 | ISEKAI Runtime |
| AI-DLC Feature | 요구 접수부터 Learn까지 개발주기, Unit, Decision, Evidence와 Checkpoint를 관리 | ISEKAI Core |
| Domain Profile | AI-DLC에 적용할 도메인 용어·규칙·Semantic mapping | Foundation |
| Product Extension | 제품 전용 타입·규칙·지표 | Project |

Product Extension은 ISEKAI 기능 배포 수단이 아니다. Feature 코드와 제어 계약은 ISEKAI가 소유하고, 설치된 Feature package와 Catalog digest는 Project-local Runtime이 고정한다.

## Feature 패키지

ISEKAI 추가 기능은 ISEKAI 저장소에서 ISEKAI Feature package로 제작하고 배포한다. 저장소 루트의 `features/catalog.json`이 배포할 package를 선택하고, 각 package는 Feature ID와 version 디렉터리 아래에 둔다.

```text
isekai/
└─ features/
   ├─ catalog.json
   └─ <feature-id>/
      └─ <version>/
         ├─ feature.json
         ├─ controller/
         ├─ schemas/
         ├─ policies/
         ├─ resources/
         ├─ migrations/
         └─ tests/
```

`feature.json`은 다음을 결박한다.

- Feature ID와 version
- Feature schema와 Core control protocol
- `active`, `preview`, `deprecated` 상태
- 제공하는 MCP action과 resource
- Core·Feature 호환 범위
- 필요한 policy와 authorization class
- package와 구성요소 digest
- migration과 rollback 계약

`distribution/release.json`은 `features/` 전체를 독립 release component와 SHA-256 digest로 결박한다. 설치기는 검증된 Catalog와 package를 Project-local `.isekai/features/`에 그대로 배치하고 `isekai.lock.json.features`에 source digest와 설치 digest를 기록한다. `doctor`는 이 디렉터리의 변조나 누락을 fail-closed로 보고한다. 사용자 홈이나 Host 전역 Plugin에는 설치하지 않는다.

현재 배포 원본은 `features/ai-dlc/0.2.1/feature.json`이다. AI-DLC controller 코드는 Core에 포함되는 `core-bundled` 방식이고, Feature manifest와 Catalog는 독립 release component로 배포된다. 새로운 기능은 controller와 검증 계약을 자기 ID·version package에 구현한 뒤 `features/catalog.json`에 등록한다. Git release 설치와 update가 Catalog 전체를 대상 Project에 배포한다.

## MCP 공통 통제면

모든 Feature는 Project-local ISEKAI Core MCP를 공통 진입점으로 사용한다.

```text
Host Agent
   │
   ▼
Project-local ISEKAI Core MCP
   ├─ Feature discovery and compatibility
   ├─ Project · Unit context binding
   ├─ Envelope · Decision · authorization
   ├─ Feature action routing
   ├─ Evidence · Checkpoint · provenance
   ├─ AI-DLC controller
   └─ Additional Feature controllers
              │
              ▼
       Feature-owned execution boundary
```

Feature별 controller는 자기 상태와 동작을 소유하지만 Core의 공통 보안·승인 경계를 우회하지 않는다. Core는 Feature 요청을 현재 Project와 Unit에 결박하고, 허용된 action·scope·risk를 검사하고, 결과를 Evidence와 Checkpoint에 연결한다.

## 현재 구현 범위

현재 구현된 공통 Feature 표면은 다음과 같다.

```text
tool:     feature_catalog
resource: isekai://runtime/features
resource: isekai://runtime/features/<feature-id>
CLI:      feature-status
```

Feature catalog와 각 manifest는 SHA-256 digest로 결박되고 새 Unit의 Context Receipt에 포함된다. Core가 이해하지 못하는 manifest, protocol 또는 authority는 fail-closed한다.

현재 Catalog에는 실행 가능한 AI-DLC Feature가 등록돼 있다. 아직 구현되지 않은 기능은 이름이나 빈 manifest를 미리 등록하지 않는다.

## 권한 불변식

- Feature는 Foundation, Project Agent level, Unit Envelope와 Human Gate를 확장하지 못한다.
- Feature catalog 발견은 실행, 파일 쓰기 또는 네트워크 권한이 아니다.
- `preview` Feature는 실행 action을 제공하지 않는다.
- Credential은 Core 밖 secret boundary에 두고 불투명 reference만 계약에 기록한다.
- Feature action과 외부 결과는 authorization, provenance, Evidence와 Checkpoint를 생략하지 못한다.
- Feature package 업데이트로 기존 Unit이 새 기능이나 권한을 암묵적으로 얻지 않으며 Receipt mismatch로 재검토를 요구한다.
