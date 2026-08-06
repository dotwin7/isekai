# ISEKAI

ISEKAI는 Codex, Claude Code, Kiro에서 사용하는 스킬 기반 AI-Driven Development Life Cycle(AI-DLC)입니다. 기존 AI 에이전트를 교체하지 않고, 에이전트 대화에 버전이 고정된 Foundation·Project·Unit 계약을 로드합니다.

현재 버전은 `0.1.0`입니다. 범용 Software Delivery Profile과 Security Domain Profile, 프로젝트 로컬 설치·업데이트·롤백, 세 런타임 Adapter의 기본 계약을 제공합니다.

## ISEKAI가 하는 일

ISEKAI를 켜면 일반 채팅 요청을 먼저 정규화하고 작업의 지속성·위험·불확실성에 따라 세 경로로 나눕니다.

| 경로 | 대상 | 처리 방식 |
|---|---|---|
| Query | 설명, 조회, 읽기 전용 분석 | 산출물 없이 바로 응답 |
| Quick Change | 작고 명확하며 되돌리기 쉬운 변경 | 최소 변경과 관련 검증 |
| Unit | 제품 계약, 여러 컴포넌트, 고위험·장기 작업 | Inception부터 Operations까지 AI-DLC 적용 |

Unit 작업에서는 Intent, Decision, Evidence, Receipt와 Checkpoint를 저장해 사람과 에이전트가 다른 세션에서도 같은 맥락을 이어갈 수 있습니다. 중요한 수명주기 전환과 고위험 판단은 사람의 명시적 결정을 요구합니다.

```text
사용자 채팅 명령
      ↓
Codex / Claude / Kiro Runtime Adapter
      ↓
프로젝트 로컬 ISEKAI Core
      ↓
Foundation + Project + Unit artifacts
```

### Plugin, Skill, Core의 차이

| 구성 | 역할 |
|---|---|
| Plugin | 호스트가 ISEKAI를 발견·설치하고 채팅 명령을 노출하는 배포 단위 |
| Skill | Plugin 안에서 에이전트가 따라야 할 호출 방식, 라우팅, 안전 규칙 |
| Core | Project·Foundation·Unit 상태를 읽고 workflow, 호환성, Decision과 Evidence를 검증하는 로컬 실행기 |

Skill이 상태의 원본은 아닙니다. `project.json`, `isekai.lock.json`, Foundation과 Unit artifact가 권위 있는 상태이며 Adapter는 Core와 handshake한 뒤 이를 사용합니다.

## 지원 런타임과 채팅 명령

| 런타임 | 통합 방식 | 대화에서 활성화 |
|---|---|---|
| Codex | Codex Plugin + Skill | `$isekai on` |
| Claude Code | Claude Plugin + namespaced Skill | `/isekai-agent-plugin:isekai on` |
| Kiro | Workspace Agent Skill | `/isekai on` |

모든 새 대화는 ISEKAI mode가 `off`인 상태로 시작합니다. `on`은 현재 대화의 Project context와 Unit 후보만 로드하고 기존 Unit을 자동으로 선택하지 않습니다. 진행 중인 Unit은 별도로 `resume`해야 합니다.

## 설치

### 요구 사항

- Git
- Python 3.11 이상
- `--register`를 사용하는 경우 선택한 Codex 또는 Claude CLI

설치 스크립트는 전역 Python package를 설치하지 않습니다. 설치할 Git tag를 임시 checkout하고 release digest를 검증한 뒤 대상 프로젝트 안에 Core, Foundation과 Runtime Adapter를 배치합니다.

아래 명령은 설치하려는 프로젝트 루트에서 실행합니다. 예시는 Codex Plugin을 등록하고 Project까지 초기화합니다. 다운로드 URL은 해당 release tag가 GitHub에 게시된 뒤 사용할 수 있습니다.

### macOS / Linux

```bash
ISEKAI_VERSION=v0.1.0
curl -fsSLo /tmp/isekai-install.sh \
  "https://raw.githubusercontent.com/dotwin7/isekai/${ISEKAI_VERSION}/scripts/install.sh"
bash /tmp/isekai-install.sh \
  --source https://github.com/dotwin7/isekai.git \
  --ref "$ISEKAI_VERSION" \
  --path . \
  --runtime codex \
  --register \
  --init \
  --profile software-delivery-profile
./.isekai/bin/isekai doctor --path .
```

### Windows PowerShell

```powershell
$IsekaiVersion = "v0.1.0"
$installer = Join-Path ([IO.Path]::GetTempPath()) "isekai-install-$IsekaiVersion.ps1"
Invoke-WebRequest `
  "https://raw.githubusercontent.com/dotwin7/isekai/$IsekaiVersion/scripts/install.ps1" `
  -OutFile $installer
& $installer `
  -Source https://github.com/dotwin7/isekai.git `
  -Ref $IsekaiVersion `
  -Path . `
  -Runtime codex `
  -Register `
  -Init `
  -Profile software-delivery-profile
py -3 .\.isekai\bin\isekai.py doctor --path .
```

### 런타임 선택과 호스트 등록

| 설치 대상 | 옵션 | 호스트 등록 |
|---|---|---|
| Codex | `--runtime codex --register` | Codex marketplace와 Plugin 등록 |
| Claude Code | `--runtime claude --register` | Project scope marketplace와 Plugin 등록 |
| Kiro | `--runtime kiro` | Workspace Skill이라 별도 등록 불필요 |
| 세 런타임 모두 | `--runtime all --register` | Codex와 Claude CLI가 모두 설치되어 있어야 함 |

`--runtime`은 반복해서 지정할 수 있습니다. `--register`는 프로젝트 밖의 호스트 설정을 변경하므로 명시했을 때만 실행됩니다. 생략하면 project-local marketplace만 만들고 필요한 네이티브 등록 명령을 설치 결과에 출력합니다. Codex 또는 Claude Adapter를 설치·업데이트한 뒤에는 새 대화를 시작해야 합니다.

기존 `project.json`이 없다면 `--init`이 manifest와 `units/`를 생성합니다. 수동으로 초기화하려면 설치된 launcher를 사용합니다.

```bash
./.isekai/bin/isekai init \
  --path . \
  --profile software-delivery-profile
```

보안 개발 규칙도 함께 사용할 프로젝트는 `--profile security-profile`을 추가할 수 있습니다. 이미 존재하는 `project.json`은 자동으로 덮어쓰지 않습니다.

### 설치 결과

선택한 런타임에 따라 다음 파일이 프로젝트에 생성됩니다.

```text
project/
├── .isekai/
│   ├── bin/                         # 프로젝트 로컬 launcher
│   ├── runtime/isekai/              # ISEKAI Core
│   ├── foundations/<version>/       # 고정된 Foundation
│   └── marketplaces/                # Codex / Claude project marketplace
├── .kiro/skills/isekai/             # Kiro를 선택한 경우
├── isekai.lock.json                 # Git ref, commit, component digest
├── project.json                     # Project 계약
└── units/                           # 지속되는 AI-DLC 작업 단위
```

설치기는 bootstrap, Core, Foundation과 Adapter의 SHA-256 tree digest를 검증하고 resolved Git commit을 `isekai.lock.json`에 고정합니다. 관리 대상이 아닌 기존 `.isekai/` 또는 Kiro Skill은 덮어쓰지 않습니다.

## 사용법

에이전트를 `project.json`이 있는 저장소에서 시작한 뒤 해당 호스트의 명령을 사용합니다.

### Codex

```text
$isekai on
$isekai status
$isekai resume --unit units/<unit-id>
$isekai off
```

### Claude Code

```text
/isekai-agent-plugin:isekai on
/isekai-agent-plugin:isekai status
/isekai-agent-plugin:isekai resume --unit units/<unit-id>
/isekai-agent-plugin:isekai off
```

### Kiro

```text
/isekai on
/isekai status
/isekai resume --unit units/<unit-id>
/isekai off
```

`on` 상태에서는 이후 요청이 자동으로 `intake`를 거쳐 Query, Quick Change 또는 Unit으로 라우팅됩니다. `off`는 자동 라우팅만 중단하며 Unit, Decision, Evidence 또는 Checkpoint를 수정하거나 삭제하지 않습니다. 명시적인 action은 mode가 꺼져 있어도 one-shot으로 실행할 수 있습니다.

Project 경로를 생략하면 Core는 현재 디렉터리, 가장 가까운 상위 디렉터리, 단일 하위 workspace 순서로 `project.json`을 찾습니다. 후보가 여러 개면 자동 선택하지 않고 사용자에게 경로 선택을 요구합니다.

## Unit lifecycle

지속적인 변경은 다음 lifecycle을 따릅니다.

```text
Inception → Human Decision → Construction → Validation
→ Release → Operations → Learn
```

- Inception에서는 Intent, Scope, Requirements, Acceptance Criteria와 Risk를 정리합니다.
- Construction에서는 승인된 범위 안에서 설계, 구현, 테스트와 Evidence를 만듭니다.
- Release와 Operations에서는 사람이 배포·롤백·고위험 결정을 승인하고 결과를 다음 Unit에 반영합니다.
- Execution Envelope 밖의 action, canonical Project scope 또는 실제 Unit stage는 Core가 거부합니다. 승인 grant는 Unit ledger에 기록되고 iteration 예산을 소모합니다.

### Execution Envelope 갱신

Envelope 승인에는 만료 창(기본 168시간, `--expires-in-hours`로 최대 720시간)과 iteration 예산이 있습니다. 둘 중 하나가 소진되면 Unit을 새로 만들지 않고 Envelope를 갱신합니다.

```bash
./.isekai/bin/isekai envelope-propose --unit units/<unit-id> ...
./.isekai/bin/isekai decision --unit units/<unit-id> --gate inception \
  --outcome approved --reference execution-envelope.json ...
./.isekai/bin/isekai envelope-approve --unit units/<unit-id>
```

교체 Envelope는 `proposed` 상태로 시작하므로 새 Decision이 승인하기 전까지 Unit은 아무 action도 authorize받지 못합니다. 만료는 authorize 시점에만 판정하며, `verify`는 승인 창이 닫힌 뒤에도 Unit을 계속 검증합니다.

## 버전 관리와 업데이트

배포 단위는 immutable Git tag와 resolved commit입니다. 먼저 변경 내용을 확인한 다음 적용합니다.

```bash
./.isekai/bin/isekai update --check --ref v0.2.0 --path .
./.isekai/bin/isekai update --ref v0.2.0 --path .
./.isekai/bin/isekai doctor --path .
```

일반 update는 Core와 Adapter만 갱신하고 Project가 고정한 Foundation은 유지합니다. Foundation 계약까지 변경하려면 diff와 사람 승인을 거친 뒤 `--include-foundation`을 명시해야 합니다. 문제가 생기면 직전 project-local 설치로 되돌릴 수 있습니다.

```bash
./.isekai/bin/isekai rollback --path .
```

## 안전 경계

- 모든 새 대화는 ISEKAI mode가 `off`입니다.
- Adapter version, Core version, protocol과 project lock이 맞지 않으면 handshake가 fail-closed합니다.
- 현재 Adapter 계약에는 자율적인 high-risk action이 없습니다.
- 쓰기 action과 lifecycle Decision은 명시적 사용자 의도 또는 사람 승인을 요구합니다.
- 고객 데이터나 민감한 원본 Evidence는 Git에서 제외되는 `units/**/evidence/raw/` 아래에 둡니다.
- 일반 update는 Foundation을 자동으로 교체하지 않습니다.

### Core가 강제하는 것과 강제하지 않는 것

Core는 Decision·Envelope·Evidence의 **일관성**을 강제합니다. Envelope는 승인 시점의 digest로 Inception Decision에 결박되고, 승인 뒤 내용이 바뀌면 authorize와 verify가 거부합니다. 예산·범위·stage를 벗어난 action도 거부합니다.

Core는 `--decided-by`에 적힌 주체가 실제 사람인지는 **검증하지 않습니다**. Decision은 Core를 호출할 수 있는 누구나 기록할 수 있는 로컬 JSON 레코드이므로, 셸 접근 권한을 가진 에이전트는 자기 Envelope를 스스로 승인할 수 있습니다. 사람의 개입은 호스트 런타임의 승인 UI(도구 실행 승인)에서 집행되며, ISEKAI가 제공하는 것은 그 판단을 감사 가능하게 기록하고 이후의 무단 변경을 탐지하는 계층입니다. 이 경계를 넘는 강제가 필요하면 원격 IAM, 보호 브랜치, 승인 시스템 같은 Core 외부 통제를 함께 사용하세요.

Plugin manifest의 `human_decision_actions`가 이 경계를 기계가 읽을 수 있게 표시합니다. Adapter는 이 목록의 action을 호출하기 전에 사용자에게 실제 확인을 받아야 합니다.

```text
decision  envelope-approve  transition  foundation-decision  foundation-promote
```

Unit과 Foundation 원장은 read-modify-write 문서이므로, 모든 변경은 Unit·Foundation 단위 파일 락으로 직렬화됩니다. 다른 프로세스가 쓰는 중이면 짧게 대기하고, 그래도 잡히지 않으면 조용히 덮어쓰는 대신 실패합니다. 락을 쥔 채 죽은 프로세스의 락은 5분 뒤 회수됩니다.

릴리스 digest 검증도 같은 성격입니다. `distribution/release.json`은 태그 안의 component가 서로 일치하는지 확인하며, 서명 검증이 아니므로 신뢰 기준점은 지정한 Git 원격과 immutable tag입니다.

## 호환성 기준

아래 버전은 최소 요구 버전이 아니라 현재 검증된 관찰 기준입니다. 호스트 CLI를 올린 뒤에는 Adapter 구조 검증과 Core의 `status`, `resume`, `verify` smoke를 다시 수행해야 합니다.

| 런타임 | 검증된 CLI 기준 | 통합 surface |
|---|---:|---|
| Codex | `0.146.0` | Codex Plugin |
| Claude Code | `2.1.220` | Claude Plugin |
| Kiro | `2.14.2` | Workspace Agent Skill |

## 저장소 구조

```text
isekai/
├── distribution/           # release manifest와 component digest
├── foundation/             # Core 계약, Profile, Policy, Evaluation
├── plugin/isekai/          # host-neutral manifest와 Runtime Adapter
├── scripts/                # POSIX / PowerShell bootstrap installer
├── src/isekai/             # 로컬 Core와 CLI
├── tests/                  # 계약, lifecycle, 설치·업데이트 테스트
├── docs/isekai.md          # canonical 설계 문서
└── project.json            # 이 저장소 자체의 Project 계약
```

## 개발과 검증

```bash
uv sync --extra test
uv run pytest
uv run python -m isekai distribution-check --root .
```

배포 component가 변경되면 tag를 만들기 전에 manifest를 다시 생성하고 검증합니다.

```bash
uv run python -m isekai distribution-build --root .
uv run python -m isekai distribution-check --root .
```

세부 workflow, Foundation 계약, artifact schema와 운영 정책은 [canonical 설계 문서](docs/isekai.md)를 참고하세요. Runtime별 세부사항은 [Codex](plugin/isekai/runtimes/codex/README.md), [Claude Code](plugin/isekai/runtimes/claude/README.md), [Kiro](plugin/isekai/runtimes/kiro/README.md) Adapter 문서에 있습니다.
