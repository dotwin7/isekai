# Architecture

- 설명: Core 내부 모듈 경계와 Project bootstrap·discovery 계약
- 문서 역할: [ISEKAI canonical 문서 집합](isekai.md)의 일부

## 프로젝트 부착형 구성

ISEKAI는 중앙 실행 서비스가 아니라 대상 프로젝트 디렉터리에 배치되는 project-local Runtime이다. 설치기는 선택한 Runtime Skill, 프로젝트 로컬 Python Core, Feature Catalog, 고정 Foundation과 lock을 함께 복사한다. 사용자는 대상 프로젝트에서 기존 Codex·Claude·Kiro를 시작하고 Skill을 명시적으로 활성화한다.

```text
Host Agent (planner and change proposer)
  └─ Project Runtime Skill (adaptive workflow driver)
       └─ Project-local Core MCP gateway
            ├─ classification, validation, records, managed execution
            ├─ Feature catalog, compatibility and action routing
            ├─ AI-DLC controller
            ├─ additional Feature controllers
            └─ Foundation + Project + Unit artifacts
```

Agent 추론을 Core에 복제하지 않는다. Runtime Skill은 Host Agent가 `intake`의 Workflow Directive를 해석해 Level-1 plan과 변경 내용을 제안하도록 만들고, Core는 승인 경계를 검증한 뒤 Unit 문서·Project edit·test를 gateway action으로 실행하고 receipt를 기록한다. 실행 경계는 lifecycle 훅이 아니라 호스트 read-only permission과 Project-local Core MCP server로 구성한다. ISEKAI가 제공하는 기능은 공통 Feature Catalog에 등록되며, 각 Feature controller는 Foundation이나 Unit Envelope보다 높은 권한을 만들 수 없다.

## Core 내부 모듈 경계

Runtime Adapter와 외부 호출자는 `isekai.workflow`, `isekai.distribution`, `isekai.foundation`, `isekai.runtime_contract`만 안정적인 façade로 사용한다. 구현 책임은 다음 경계로 분리한다.

| 영역 | 구현 경계 |
|---|---|
| Project와 Unit workflow | `workflow/project.py`, `workflow/routing.py`, `workflow/session.py`, `workflow/active_binding.py`, `workflow/unit/` |
| ISEKAI Features | `workflow/features.py`가 AI-DLC와 추가 기능 manifest를 검증하고 digest-bound catalog와 MCP resource를 생성 |
| Project Knowledge | `workflow/project_knowledge.py` service, `project_knowledge_schema.py` 검증·선택·호환 정책, `project_knowledge_storage.py` 안전한 파일 경계, `project_knowledge_observability.py` candidate·Decision 상태 결합 |
| 배포와 설치 | `distribution/release.py`, `distribution/marketplace.py`, `distribution/install.py`, `distribution/execution_profile.py`, `distribution/git.py` |
| Core tool gateway | `mcp_server.py`, `workflow/unit/managed_execution.py` |
| Foundation | `foundation/types.py`, `foundation/validation.py`, `foundation/evaluation.py`, `foundation/promotion.py` |
| Project Runtime | `runtime/actions.py`가 host-neutral action을 실행하고 `runtime_contract.py`가 protocol envelope를 생성 |
| CLI | `cli/parser.py`가 명령 표면을, `cli/runtime_request.py`가 runtime payload와 exit code를 담당 |

루트에는 기존 import 경로를 보존하는 얇은 호환 façade만 둔다. 새 기능은 façade에 도메인 로직을 추가하지 않고 해당 구현 경계에 둔다. 구조 테스트는 전체 패키지를 재귀 검사해 구현 모듈 750줄, façade 150줄 상한과 module-level import cycle 부재를 CI에서 검증한다.

## Project bootstrap과 discovery

ISEKAI 적용 저장소는 기본적으로 루트에 `project.json`을 둔다. Agent CLI를 저장소 루트나 하위 디렉터리에서 실행하면 별도 경로 없이 Project를 선택할 수 있다.

```text
project-root/
├─ project.json
├─ .isekai/features/                  # release가 배포한 Feature Catalog와 package
├─ .isekai-runtime/active-unit.json  # ignored Core enforcement state
├─ foundation/ 또는 project.json의 foundation_path
├─ units/
├─ src/
└─ tests/
```

Project가 없으면 명시적 사용자 확인 후 `isekai init --path PATH`로 manifest와 `units/`를 생성한다. Init은 Foundation·Profile을 preflight하고 기존 `project.json`을 덮어쓰지 않으며 실패한 manifest를 rollback한다. `unit-init`은 숨김 staging 디렉터리에 전체 artifact를 작성한 뒤 최종 Unit 경로로 rename하므로 중간 I/O 실패가 discover 가능한 부분 Unit을 남기지 않는다.

Runtime `unit-init`, `resume`, 또는 첫 persistent Unit action은 Project별 active Unit 결박을 `.isekai-runtime/active-unit.json`에 기록한다. 결박 원장은 bind·detach·learned 사건의 SHA-256 chain, Unit locator와 caller-attested detach 판단을 보존하며 symlink·변조·동시 갱신에 fail-closed한다. 결박을 잡은 동안 Core는 routing과 Unit 생성, 형제 Unit persistent action을 차단하고 같은 lock을 action 완료까지 유지한다. 이 파일은 대화 mode나 공유 lifecycle artifact가 아니라 로컬 Core enforcement state이므로 Git에서 제외한다. 외부 절대 output의 Unit은 Context Receipt가 같은 Project를 결박할 때만 absolute locator로 저장한다.

Project discovery 순서는 direct current directory → nearest ancestor → filtered descendants다. 중첩 Project에서는 가장 가까운 ancestor manifest를 사용한다. descendant 후보가 하나면 선택할 수 있지만 둘 이상이면 모든 후보를 표시하고 `--project`로 명시적 선택을 요구한다. `.git`, build output, dependency, runtime과 `units/` 디렉터리는 descendant 검색에서 제외한다.

`project.json.parent`가 Project root다. 명시적 manifest 파일도 canonical 이름인 `project.json`만 허용하고, manifest 자체는 symlink path segment가 없는 single-link regular file이어야 한다. `id`, `version`, `foundation_path`, Profile ID와 `maximum_agent_level`을 검증해 생성된 Context Receipt와 authorization이 같은 Project 계약을 사용하게 한다. 새 Receipt의 Project manifest locator는 Unit 디렉터리를 기준으로 하고 Product Extension locator는 Project root를 기준으로 하므로 저장소를 이동하거나 clone해도 경로 자체는 계약 변경으로 취급하지 않는다. 기존 절대 경로 Receipt는 `unit-migrate`가 Project ID, Foundation digest, Profile, Product Extension과 규칙 내용이 모두 같을 때만 path-only로 재결박한다. ISEKAI Feature는 Project Extension locator가 아니라 설치된 `.isekai/features/` component digest와 Catalog digest로 Receipt에 결박한다. `maximum_agent_level`은 `L0`의 read-only, `L1`의 bounded local read·edit·test, 또는 `L2`의 exact development/test external API 상한이며, Unit의 Envelope는 이 상한을 넓힐 수 없다. L2의 key 원문은 Core 밖 호스트 secret 경계에 남고 Envelope에는 불투명 참조만 결박된다. `unit-init`의 output을 생략하면 `project-root/units/`를 사용한다. 상대 output은 Project root 기준이며 `..` 또는 symlink로 root를 벗어나면 거부한다. 명시적 절대 output은 외부 저장 의도로 간주해 허용한다. `foundation_path`는 조직 공통 Foundation을 공유할 수 있도록 Project 외부 상대·절대 경로를 허용한다. Unit metadata, Decision, Receipt, Checkpoint와 검증 Evidence는 Stage 1의 공유 Persistent Context로 버전 관리하며, 필수 artifact 경로의 symlink는 Unit 외부 상태를 읽지 않도록 fail-closed로 거부한다. 고객 데이터나 민감한 원본 출력은 `units/**/evidence/raw/` 아래에 두고 Git에서 제외한다.
