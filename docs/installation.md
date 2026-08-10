# Installation

- 설명: Git release 설치와 프로젝트 버전 고정, update·rollback 계약
- 문서 역할: [ISEKAI canonical 문서 집합](isekai.md)의 일부

## Git release 설치와 프로젝트 버전 고정

공식 배포 단위는 immutable Git tag와 commit이다. 각 tag는 `distribution/release.json`에 bootstrap script, Core, host-neutral Runtime contract, Catalog, Foundation과 Kiro·Claude·Codex Adapter의 버전·경로·SHA-256 tree digest를 등록한다. Runtime contract component는 공통 manifest·호환성 Evidence·Runtime Skill 생성 원본까지 결박하고, Catalog component는 저장소 `catalog/catalog.json`과 모든 versioned package를 결박한다. `distribution-check`는 component의 파일 경로·bytes·실행 비트를 결박하고 component의 symlink·hardlink·특수 파일을 거부하며, package, Runtime과 Foundation manifest에서 다시 만든 canonical ID·version·path metadata도 대조한다. `distribution/release.json`, `pyproject.toml`과 canonical source manifest는 symlink path segment가 없는 single-link regular file이어야 한다. 설치기는 tag를 임시 checkout하고 이 검증을 통과한 뒤에만 Project를 변경한다.

Distribution, Core, Foundation과 각 Adapter version은 독립적으로 진화한다. 상호 호환성은 같은 숫자 버전이 아니라 `protocol_version`, 지원 Project/Foundation schema와 `isekai.lock.json`의 component pin으로 판정한다.

```bash
curl -fsSLo /tmp/isekai-install.sh \
  https://raw.githubusercontent.com/dotwin7/isekai/v0.2.1/scripts/install.sh
bash /tmp/isekai-install.sh \
  --source https://github.com/dotwin7/isekai.git \
  --ref v0.2.1 \
  --path . \
  --runtime all \
  --init \
  --maximum-agent-level L1 \
  --profile software-delivery-profile
./.isekai/bin/isekai doctor --path .
```

Windows PowerShell에서는 같은 tag의 스크립트를 사용한다.

```powershell
$installer = Join-Path ([IO.Path]::GetTempPath()) "isekai-install-v0.2.1.ps1"
Invoke-WebRequest `
  https://raw.githubusercontent.com/dotwin7/isekai/v0.2.1/scripts/install.ps1 `
  -OutFile $installer
& $installer `
  -Source https://github.com/dotwin7/isekai.git `
  -Ref v0.2.1 `
  -Path . `
  -Runtime all `
  -Init `
  -MaximumAgentLevel L1 `
  -Profile software-delivery-profile
py -3 .\.isekai\bin\isekai.py doctor --path .
```

`--profile software-delivery-profile`은 AI-DLC 워크플로에 필요한 Software Delivery Profile(Requirement, Component, Change, Build, ReleaseDecision 도메인 타입)을 활성화한다. Profile 없이도 Core 기계는 동작하지만 도메인 규칙이 없는 빈 규칙 세트로 실행되므로 소프트웨어 개발 프로젝트에서는 기본으로 포함한다. 보안 개발 규칙도 함께 사용할 프로젝트는 `--profile security-profile`을 추가할 수 있다.

bootstrap은 전역 Python package를 설치하지 않는다. Git과 Python 3.11+만 확인한 뒤 지정한 tag를 임시 checkout하고, 해당 checkout의 설치 엔진을 실행한다. tag 입력은 `check-ref-format`을 통과한 실제 tag 이름이어야 하며 `^`, `~`, `^{}` 같은 revision 표현은 허용하지 않는다. 설치 엔진은 checkout의 origin, immutable ref, HEAD, clean worktree와 기록할 commit이 모두 일치하는지 확인해 다른 저장소나 수정된 checkout의 파일을 신뢰된 commit으로 기록하지 않는다. 같은 검증은 공개 `install_from_checkout` API에도 적용된다. `--init`은 설치 뒤 `project.json`이 없을 때 Project 초기화까지 수행한다. 초기화 시 agent level을 생략하면 read-only인 `L0`이 적용된다. 로컬 편집·테스트에는 `L1`, 승인된 개발·테스트 외부 API에는 `L2`를 `--init`/`-Init`과 함께 명시한다. L2 key 원문은 프로젝트나 명령 인자에 넣지 않고 호스트 secret store에 둔다.

로컬 `managed-test`에는 OS sandbox provider가 필수다. macOS는 시스템 `/usr/bin/sandbox-exec`(Seatbelt)을 사용하고 Linux는 배포판 패키지의 `bwrap`(Bubblewrap)을 설치해야 한다(예: `sudo apt-get install bubblewrap`). Core는 provider 실행 preflight가 실패하면 테스트 command를 시작하지 않는다. 실행 시 stdout/stderr 합계가 8 MiB에 도달하거나 요청한 wall-clock timeout을 넘으면 process group을 종료하며, OS별 hard resource limit도 적용한다. Windows에는 현재 로컬 provider가 없으므로 보호된 CI나 원격 sandbox의 Evidence를 제출한다.

macOS Seatbelt에는 PID namespace가 없다. command가 의도적으로 새 session을 만들고 daemonize하면 Core의 즉시 process-group 정리를 벗어날 수 있지만, 그 process는 상속한 Seatbelt와 hard resource policy 아래에 남는다. 적대적 code의 완전한 process-lifetime 격리가 필요하면 Linux Bubblewrap 또는 별도 VM/원격 sandbox를 사용한다.

설치는 `.isekai/runtime/isekai`, `.isekai/catalog/`, `.isekai/foundations/<version>/`와 선택한 Runtime의 프로젝트 Skill을 준비하고 `isekai.lock.json`에 Git source·tag·resolved commit과 설치된 component digest를 기록한다. Catalog는 ISEKAI release의 `catalog/`에서 복사되며 Core는 설치된 `.isekai/catalog/catalog.json`만 읽는다. Codex는 `.agents/skills/isekai`, Claude Code는 `.claude/skills/isekai`, Kiro는 `.kiro/skills/isekai`에서 프로젝트 Adapter를 직접 발견한다. 각 프로젝트가 자기 lock으로 Core·Catalog와 Skill 버전을 고정하므로 동일한 호스트에서도 저장소별 ISEKAI 버전을 사용할 수 있다.

설치 스크립트는 선택한 Runtime의 Project 실행 보호 설정을 자동으로 적용한다. Codex 설정은 Project config를 read-only로 만들고, Claude 설정은 direct Edit·Write·NotebookEdit·Bash를 deny하며, Kiro 설정은 `read`와 `@mcp`만 노출하는 `isekai-core` agent를 만든다. 세 Runtime 모두 Project-local `.isekai/bin/isekai mcp-serve`를 연결하며 lifecycle hook을 등록하지 않는다. 설치 뒤에는 새 대화를 시작하고 ISEKAI `on`/`off`만 사용한다. Kiro는 생성된 `isekai-core` agent를 선택한다. 문제가 의심되면 `doctor --path .`으로 확인하고 `doctor --path . --fix`로 설치된 모든 Runtime의 보호 설정을 복구한다.

Adapter `handshake`는 Project 실행 보호 설정의 read-only 선언이나 MCP entry가 없거나 변조되면 fail-closed한다. 다만 더 높은 우선순위의 Host flag나 조직 managed policy가 실제 process에서 이를 덮어썼는지는 Project-local Core가 증명할 수 없으므로, 현재 세션에 direct writer가 보이면 중단해야 한다.

`project.json`과 `isekai.lock.json`은 single-link regular file로만 읽으며 lock의 source, release, protocol, component, Adapter, path와 SHA-256 필드 타입을 사용 전에 검증한다. malformed lock은 `doctor`에서 비정상 상태로 보고하고 install·update·handshake는 처리되지 않은 타입 예외 없이 fail-closed한다. 같은 로컬 저장소의 경로와 authority가 없거나 `localhost`인 `file://` URL, 원격 URL의 hostname 대소문자·DNS 종단점·기본 포트 차이는 동일 source로 정규화한다. 사용자 정보·port·다른 hostname을 가진 `file://` URL은 플랫폼별 해석 차이를 피하기 위해 거부한다. 따라서 기존 lock의 tag가 다른 commit으로 이동했다면 source나 ref 표기 변경으로 검사를 우회할 수 없다. 설치기는 marketplace 선언이나 사용자 홈 설정을 만들거나 수정하지 않는다. 선택 Runtime의 Project-local 실행 보호 설정과 MCP 연결만 변경한다. 이전 marketplace 기반 설치를 update할 때만 lock이 소유권을 증명하는 legacy ISEKAI 선언을 제거하며, 다른 host 설정은 보존한다. `isekai.lock.json`, `.isekai/`와 workspace Adapter는 팀이 같은 계약을 재현하도록 Git에 포함한다.

설치 명령은 사용자 홈의 marketplace·Skill·host 설정을 변경하지 않는다. 대신 선택한 Runtime의 Project-local 실행 보호 설정을 merge하고 원래 bytes를 `.isekai/host-custody/state.json`에 보존한다. ISEKAI 생명주기의 프로젝트 경로는 repo/project/workspace Skill이므로 전역 등록에 의존하지 않는다. 각 Adapter는 실행 시 현재 프로젝트의 `.isekai/bin/isekai`, `isekai.lock.json`, 유효한 Core 전용 실행 보호 설정을 요구한다. Adapter 업데이트 뒤에는 host가 새 Skill 계약을 읽도록 새 대화를 시작한다.

## Update와 rollback

### 0.1.x에서 0.2.1으로 업그레이드

업그레이드는 대상 Project 루트에서 그 Project에 설치된 launcher로 실행한다. 시작 전에 사용자 작업과 project-local ISEKAI 파일의 변경을 커밋하거나 백업한다. `doctor`가 현재 lock, 설치 파일과 Foundation pin의 무결성을 확인하고, `update --check`는 target commit과 component 변경을 읽기 전용으로 보고한다. 둘 중 하나가 실패하면 실제 update를 진행하기 전에 현재 설치나 source/ref 문제를 해결한다.

```bash
./.isekai/bin/isekai doctor --path .
./.isekai/bin/isekai update --check --ref v0.2.1 --path .
./.isekai/bin/isekai update --ref v0.2.1 --path .
./.isekai/bin/isekai doctor --path .
```

Windows에서는 같은 Project에서 launcher만 `py -3 .\.isekai\bin\isekai.py`로 바꾸고 동일한 인자를 사용한다. 정상 완료 후 `isekai.lock.json`, `.isekai/`, 선택한 Runtime Skill과 host 설정 diff를 검토해 함께 커밋한다. 새 Adapter가 설치되면 Codex·Claude·Kiro가 새 Skill 계약을 다시 읽도록 새 대화를 시작한다. 이전 marketplace 기반 0.1.0 설치는 lock이 ISEKAI 소유권을 증명하는 legacy 선언만 제거하며 다른 host 설정은 보존한다.

기본 update는 Core와 Adapter만 교체하고 Project가 고정한 Foundation을 유지한다. 0.2.1 Foundation의 L2 외부 API 계약까지 채택하려면 진행 중 Unit이 이전 Foundation version·digest에 고정되어 있지 않은지 먼저 확인하고, Foundation diff에 대한 사람 승인을 받은 다음 두 opt-in을 함께 사용한다.

```bash
./.isekai/bin/isekai update --check --ref v0.2.1 --path . \
  --include-foundation --adopt-foundation
./.isekai/bin/isekai update --ref v0.2.1 --path . \
  --include-foundation --adopt-foundation
./.isekai/bin/isekai doctor --path .
```

`--include-foundation`은 target release의 Foundation을 설치 대상으로 포함하고, `--adopt-foundation`은 현재 Project 계약과 다른 version·digest를 채택한다는 명시적 확인이다. Foundation을 바꾸면 이전 계약으로 생성된 진행 중 Unit은 별도로 검토된 contract migration 전까지 resume할 수 없으므로 완료·정리하거나 기존 Foundation을 유지한다.

Core·Adapter·Foundation 업그레이드는 `project.json.maximum_agent_level`을 자동으로 높이지 않는다. L2가 실제로 필요한 Project만 별도 계약 검수 후 값을 `L2`로 변경한다. 기존 Unit은 Context Receipt에 고정된 level을 유지하며 새 Unit부터 변경된 상한을 사용한다. secret 원문은 `project.json`, Unit artifact 또는 명령 인자에 넣지 않는다.

문제가 생기면 update가 만든 무결성 결박 snapshot으로 직전 설치를 복원하고 다시 검사한다.

```bash
./.isekai/bin/isekai rollback --path .
./.isekai/bin/isekai doctor --path .
```

update는 이전 managed install, lock, workspace Adapter, host 설정과 Project manifest snapshot 전체의 digest를 새 lock의 `rollback` 항목에 결박한다. rollback은 현재 설치와 이 snapshot digest를 모두 확인한 뒤에만 복원하고, 복원 과정에서 만드는 redo snapshot도 복원된 lock에 새 digest로 결박한다. 이때 사용자 소유 `project.json`은 update 이후의 생성·수정 내용을 보존하며, Foundation 계약이 이전 버전으로 바뀌는 경우에만 snapshot의 `foundation_path`를 다시 연결한다. digest가 없거나 달라진 legacy·변조 snapshot은 복원하지 않는다.

새 Unit의 Context Receipt는 Unit 디렉터리 기준의 portable Project manifest locator와 Project 상대 Extension locator를 사용하므로 Project 디렉터리를 함께 이동하거나 clone해도 그대로 resume할 수 있다. 이전 버전의 절대 경로 Receipt나 Project와 별도로 이동한 외부 Unit은 계약 내용이 동일한 경우에만 다음 path-only migration으로 다시 결박한다.

```bash
./.isekai/bin/isekai unit-migrate --project . --unit units/<unit-id>
```

`unit-migrate`는 Project ID, Foundation version·digest, Profile, Extension 내용과 적용 규칙이 하나라도 달라지면 실패한다. Foundation 또는 Project 계약을 바꾸는 migration 명령이 아니며, 그러한 변경은 별도 Unit과 사람 검토가 필요하다.
