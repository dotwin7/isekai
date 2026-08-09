# Installation

- 설명: Git release 설치와 프로젝트 버전 고정, update·rollback 계약
- 문서 역할: [ISEKAI canonical 문서 집합](isekai.md)의 일부

## Git release 설치와 프로젝트 버전 고정

공식 배포 단위는 immutable Git tag와 commit이다. 각 tag는 `distribution/release.json`에 bootstrap script, Core, Foundation, Kiro·Claude·Codex Adapter의 버전·경로·SHA-256 tree digest를 등록한다. `distribution-check`는 component의 파일 경로·bytes·실행 비트를 결박하고 component의 symlink·hardlink·특수 파일을 거부하며, package, plugin, Foundation과 Runtime manifest에서 다시 만든 canonical ID·version·path metadata도 대조한다. `distribution/release.json`, `pyproject.toml`과 canonical source manifest는 symlink path segment가 없는 single-link regular file이어야 한다. 설치기는 tag를 임시 checkout하고 이 검증을 통과한 뒤에만 Project를 변경한다.

Distribution, Core, Foundation과 각 Adapter version은 독립적으로 진화한다. 상호 호환성은 같은 숫자 버전이 아니라 `protocol_version`, 지원 Project/Foundation schema와 `isekai.lock.json`의 component pin으로 판정한다.

```bash
curl -fsSLo /tmp/isekai-install.sh \
  https://raw.githubusercontent.com/dotwin7/isekai/v0.1.0/scripts/install.sh
bash /tmp/isekai-install.sh \
  --source https://github.com/dotwin7/isekai.git \
  --ref v0.1.0 \
  --path . \
  --runtime all \
  --init \
  --maximum-agent-level L1
./.isekai/bin/isekai doctor --path .
```

Windows PowerShell에서는 같은 tag의 스크립트를 사용한다.

```powershell
$installer = Join-Path ([IO.Path]::GetTempPath()) "isekai-install-v0.1.0.ps1"
Invoke-WebRequest `
  https://raw.githubusercontent.com/dotwin7/isekai/v0.1.0/scripts/install.ps1 `
  -OutFile $installer
& $installer `
  -Source https://github.com/dotwin7/isekai.git `
  -Ref v0.1.0 `
  -Path . `
  -Runtime all `
  -Init `
  -MaximumAgentLevel L1
py -3 .\.isekai\bin\isekai.py doctor --path .
```

bootstrap은 전역 Python package를 설치하지 않는다. Git과 Python 3.11+만 확인한 뒤 지정한 tag를 임시 checkout하고, 해당 checkout의 설치 엔진을 실행한다. tag 입력은 `check-ref-format`을 통과한 실제 tag 이름이어야 하며 `^`, `~`, `^{}` 같은 revision 표현은 허용하지 않는다. 설치 엔진은 checkout의 origin, immutable ref, HEAD, clean worktree와 기록할 commit이 모두 일치하는지 확인해 다른 저장소나 수정된 checkout의 파일을 신뢰된 commit으로 기록하지 않는다. 같은 검증은 공개 `install_from_checkout` API에도 적용된다. `--init`은 설치 뒤 `project.json`이 없을 때 Project 초기화까지 수행한다. 초기화 시 agent level을 생략하면 read-only인 `L0`이 적용된다. 로컬 편집·테스트가 필요하면 POSIX의 `--maximum-agent-level L1` 또는 PowerShell의 `-MaximumAgentLevel L1`을 `--init`/`-Init`과 함께 명시한다.

설치는 `.isekai/runtime/`, `.isekai/foundations/<version>/`, Codex·Claude Plugin package와 세 Runtime의 workspace Skill을 준비하고 `isekai.lock.json`에 Git source·tag·resolved commit과 설치된 component digest를 기록한다. Codex는 `.agents/skills/isekai`, Claude Code는 `.claude/skills/isekai`, Kiro는 `.kiro/skills/isekai`에서 프로젝트 Adapter를 직접 발견한다. Codex·Claude의 완전한 Plugin package는 `.isekai/marketplaces/`에 별도 보존하며 package와 workspace Skill digest를 모두 lock에 결박한다.

`project.json`과 `isekai.lock.json`은 single-link regular file로만 읽으며 lock의 source, release, protocol, component, Adapter, path와 SHA-256 필드 타입을 사용 전에 검증한다. malformed lock은 `doctor`에서 비정상 상태로 보고하고 install·update·handshake는 처리되지 않은 타입 예외 없이 fail-closed한다. 같은 로컬 저장소의 경로와 authority가 없거나 `localhost`인 `file://` URL, 원격 URL의 hostname 대소문자·DNS 종단점·기본 포트 차이는 동일 source로 정규화한다. 사용자 정보·port·다른 hostname을 가진 `file://` URL은 플랫폼별 해석 차이를 피하기 위해 거부한다. 따라서 기존 lock의 tag가 다른 commit으로 이동했다면 source나 ref 표기 변경으로 검사를 우회할 수 없다. `.agents/plugins/marketplace.json`과 `.claude/settings.json`은 Plugin을 host marketplace에서 별도로 설치할 수 있게 하는 프로젝트 선언이다. 비기본 marketplace는 host의 신뢰·설치 경계를 그대로 따르며, 설치기는 파일 복사만으로 Plugin이 host-global 설치되었다고 주장하지 않는다. `isekai.lock.json`, `.isekai/`와 workspace Adapter는 팀이 같은 계약을 재현하도록 Git에 포함한다. 기존 설정의 다른 항목과 관리 대상이 아닌 ISEKAI 항목은 덮어쓰지 않는다.

설치기는 사용자 홈의 marketplace, Skill 또는 host 설정을 변경하지 않는다. Host가 Plugin을 별도로 활성화하며 만드는 자체 cache는 host가 관리하지만, ISEKAI 생명주기의 프로젝트 기본 경로는 repo/project Skill이므로 전역 등록에 의존하지 않는다. 각 Adapter는 실행 시 현재 프로젝트의 `.isekai/bin/isekai`와 `isekai.lock.json`을 요구한다. Adapter 업데이트 뒤에는 host가 새 Skill 계약을 읽도록 새 대화를 시작한다.

## Update와 rollback

```bash
./.isekai/bin/isekai update --check --ref v0.2.0 --path .
./.isekai/bin/isekai update --ref v0.2.0 --path .
./.isekai/bin/isekai rollback --path .
```

`update --check`는 target commit과 component 변경을 읽기 전용으로 보고한다. 일반 update는 Core와 Adapter만 갱신하고 Project가 고정한 Foundation은 유지한다. Foundation 변경은 diff와 사람 승인을 거쳐 `--include-foundation`을 명시해야 하며, 기존 외부 Foundation과 다르면 `--adopt-foundation`도 요구한다. update는 이전 managed install, lock, workspace Adapter, host 설정과 Project manifest snapshot 전체의 digest를 새 lock의 `rollback` 항목에 결박한다. rollback은 현재 설치와 이 snapshot digest를 모두 확인한 뒤에만 복원하고, 복원 과정에서 만드는 redo snapshot도 복원된 lock에 새 digest로 결박한다. digest가 없거나 달라진 legacy·변조 snapshot은 복원하지 않는다. 진행 중 Unit은 생성 당시 Foundation version과 contract digest를 유지하고, 현재 Project 계약과 다르면 별도로 검토된 contract migration 전까지 resume을 차단한다.

새 Unit의 Context Receipt는 Unit 디렉터리 기준의 portable Project manifest locator와 Project 상대 Extension locator를 사용하므로 Project 디렉터리를 함께 이동하거나 clone해도 그대로 resume할 수 있다. 이전 버전의 절대 경로 Receipt나 Project와 별도로 이동한 외부 Unit은 계약 내용이 동일한 경우에만 다음 path-only migration으로 다시 결박한다.

```bash
./.isekai/bin/isekai unit-migrate --project . --unit units/<unit-id>
```

`unit-migrate`는 Project ID, Foundation version·digest, Profile, Extension 내용과 적용 규칙이 하나라도 달라지면 실패한다. Foundation 또는 Project 계약을 바꾸는 migration 명령이 아니며, 그러한 변경은 별도 Unit과 사람 검토가 필요하다.
