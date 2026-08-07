# Installation

- 설명: Git release 설치와 프로젝트 버전 고정, update·rollback 계약
- 문서 역할: [ISEKAI canonical 문서 집합](isekai.md)의 일부

## Git release 설치와 프로젝트 버전 고정

공식 배포 단위는 immutable Git tag와 commit이다. 각 tag는 `distribution/release.json`에 bootstrap script, Core, Foundation, Kiro·Claude·Codex Adapter의 버전·경로·SHA-256 tree digest를 등록한다. 설치기는 tag를 임시 checkout하고 모든 digest를 검증한 뒤에만 Project를 변경한다.

Distribution, Core, Foundation과 각 Adapter version은 독립적으로 진화한다. 상호 호환성은 같은 숫자 버전이 아니라 `protocol_version`, 지원 Project/Foundation schema와 `isekai.lock.json`의 component pin으로 판정한다.

```bash
curl -fsSLo /tmp/isekai-install.sh \
  https://raw.githubusercontent.com/dotwin7/isekai/v0.1.0/scripts/install.sh
bash /tmp/isekai-install.sh \
  --source https://github.com/dotwin7/isekai.git \
  --ref v0.1.0 \
  --path . \
  --runtime all \
  --init
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
  -Init
py -3 .\.isekai\bin\isekai.py doctor --path .
```

bootstrap은 전역 Python package를 설치하지 않는다. Git과 Python 3.11+만 확인한 뒤 지정한 tag를 임시 checkout하고, 해당 checkout의 설치 엔진을 실행한다. `--init`은 설치 뒤 `project.json`이 없을 때 Project 초기화까지 수행한다.

설치는 `.isekai/runtime/`, `.isekai/foundations/<version>/`, Codex·Claude project marketplace와 `.kiro/skills/isekai/`를 준비하고 `isekai.lock.json`에 Git source·tag·resolved commit과 설치된 component digest를 기록한다. `isekai.lock.json`, `.isekai/`와 workspace Adapter는 팀이 같은 계약을 재현하도록 Git에 포함한다. 기존 `.isekai/`나 Kiro Skill이 ISEKAI 관리 대상으로 확인되지 않으면 덮어쓰지 않는다.

Codex·Claude의 host 등록은 저장소 밖 상태를 변경할 수 있으므로 `--register`를 명시한 경우에만 네이티브 marketplace 명령을 실행한다. 등록하지 않은 설치는 실행할 명령을 JSON 결과로 반환한다. Adapter 업데이트 뒤에는 host가 새 Skill을 읽도록 새 대화를 시작한다.

## Update와 rollback

```bash
./.isekai/bin/isekai update --check --ref v0.2.0 --path .
./.isekai/bin/isekai update --ref v0.2.0 --path .
./.isekai/bin/isekai rollback --path .
```

`update --check`는 target commit과 component 변경을 읽기 전용으로 보고한다. 일반 update는 Core와 Adapter만 갱신하고 Project가 고정한 Foundation은 유지한다. Foundation 변경은 diff와 사람 승인을 거쳐 `--include-foundation`을 명시해야 하며, 기존 외부 Foundation과 다르면 `--adopt-foundation`도 요구한다. 진행 중 Unit은 생성 당시 Foundation version과 contract digest를 유지하고, 현재 Project 계약과 다르면 명시적 migration 전까지 resume을 차단한다.
