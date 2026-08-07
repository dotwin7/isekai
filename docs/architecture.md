# Architecture

- 설명: Core 내부 모듈 경계와 Project bootstrap·discovery 계약
- 문서 역할: [ISEKAI canonical 문서 집합](isekai.md)의 일부

## Core 내부 모듈 경계

Runtime Adapter와 외부 호출자는 `isekai.workflow`, `isekai.distribution`, `isekai.foundation`, `isekai.plugin_contract`만 안정적인 façade로 사용한다. façade는 기존 import와 응답 계약을 유지하고, 구현 책임은 다음 경계로 분리한다.

| 영역 | 구현 경계 |
|---|---|
| Project와 Unit workflow | `workflow/project.py`, `workflow/routing.py`, `workflow/session.py`, `workflow/unit/` |
| 배포와 설치 | `distribution/release.py`, `distribution/marketplace.py`, `distribution/install.py`, `distribution/git.py` |
| Foundation | `foundation/types.py`, `foundation/validation.py`, `foundation/evaluation.py`, `foundation/promotion.py` |
| Agent Plugin | `plugin/actions.py`가 host-neutral action을 실행하고 `plugin_contract.py`가 protocol envelope를 생성 |
| CLI | `cli/parser.py`가 명령 표면을, `cli/plugin_request.py`가 plugin payload와 exit code를 담당 |

루트에는 기존 import 경로를 보존하는 얇은 호환 façade만 둔다. 새 기능은 façade에 도메인 로직을 추가하지 않고 해당 구현 경계에 둔다. 구조 테스트는 전체 패키지를 재귀 검사해 구현 모듈 750줄, façade 150줄 상한과 module-level import cycle 부재를 CI에서 검증한다.

## Project bootstrap과 discovery

ISEKAI 적용 저장소는 기본적으로 루트에 `project.json`을 둔다. Agent CLI를 저장소 루트나 하위 디렉터리에서 실행하면 별도 경로 없이 Project를 선택할 수 있다.

```text
project-root/
├─ project.json
├─ foundation/ 또는 project.json의 foundation_path
├─ units/
├─ src/
└─ tests/
```

Project가 없으면 명시적 사용자 확인 후 `isekai init --path PATH`로 manifest와 `units/`를 생성한다. Init은 Foundation·Profile을 preflight하고 기존 `project.json`을 덮어쓰지 않으며 실패한 manifest를 rollback한다. `unit-init`은 숨김 staging 디렉터리에 전체 artifact를 작성한 뒤 최종 Unit 경로로 rename하므로 중간 I/O 실패가 discover 가능한 부분 Unit을 남기지 않는다.

Project discovery 순서는 direct current directory → nearest ancestor → filtered descendants다. 중첩 Project에서는 가장 가까운 ancestor manifest를 사용한다. descendant 후보가 하나면 선택할 수 있지만 둘 이상이면 모든 후보를 표시하고 `--project`로 명시적 선택을 요구한다. `.git`, build output, dependency, runtime과 `units/` 디렉터리는 descendant 검색에서 제외한다.

`project.json.parent`가 Project root다. 명시적 manifest 파일도 canonical 이름인 `project.json`만 허용하고, manifest 자체는 symlink path segment가 없는 single-link regular file이어야 한다. `id`, `version`, `foundation_path`, Profile ID의 문자열 타입을 검증해 생성된 Context Receipt와 authorization이 같은 Project 계약을 사용하게 한다. `unit-init`의 output을 생략하면 `project-root/units/`를 사용한다. 상대 output은 Project root 기준이며 `..` 또는 symlink로 root를 벗어나면 거부한다. 명시적 절대 output은 외부 저장 의도로 간주해 허용한다. `foundation_path`는 조직 공통 Foundation을 공유할 수 있도록 Project 외부 상대·절대 경로를 허용한다. Unit metadata, Decision, Receipt, Checkpoint와 검증 Evidence는 Stage 1의 공유 Persistent Context로 버전 관리하며, 필수 artifact 경로의 symlink는 Unit 외부 상태를 읽지 않도록 fail-closed로 거부한다. 고객 데이터나 민감한 원본 출력은 `units/**/evidence/raw/` 아래에 두고 Git에서 제외한다.
