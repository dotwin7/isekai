# ISEKAI

ISEKAI는 Codex, Claude Code, Kiro에서 사용하는 스킬 기반 AI-Driven Development Life Cycle(AI-DLC)입니다. 기존 AI 에이전트를 교체하지 않고, 에이전트 대화에 버전이 고정된 Foundation·Project·Unit 계약을 로드합니다.

배포 형태는 대상 프로젝트에 라이브러리처럼 붙고 버전이 고정되는 프로젝트 로컬 AI-DLC Runtime입니다. 설치된 Runtime Skill을 따르는 기존 에이전트가 계획·질문을 주도하고, ISEKAI Core는 분류·승인 경계·상태·증거를 검증하며 승인된 파일 변경을 직접 적용합니다. 별도 Agent Brain이나 lifecycle 훅을 전제로 하지 않습니다.

현재 버전은 `0.2.1`입니다. 범용 Software Delivery Profile과 Security Domain Profile, 프로젝트 로컬 설치·업데이트·롤백, 세 런타임 Adapter, L2 개발·테스트 외부 API 계약과 ISEKAI 추가 기능의 공통 Catalog·제어 기반을 제공합니다.

## ISEKAI가 하는 일

ISEKAI를 켜면 일반 채팅 요청을 먼저 정규화하고 작업의 지속성·위험·불확실성에 따라 세 경로로 나눕니다.

| 경로 | 대상 | 처리 방식 |
|---|---|---|
| Query | 설명, 조회, 읽기 전용 분석 | 산출물 없이 바로 응답 |
| Quick Change | 작고 명확하며 되돌리기 쉬운 변경 | 최소 변경과 관련 검증 |
| Unit | 제품 계약, 여러 컴포넌트, 고위험·장기 작업 | Inception부터 Operations까지 AI-DLC 적용 |

Unit 작업에서는 Intent, Decision, Evidence, Receipt와 Checkpoint를 저장해 사람과 에이전트가 다른 세션에서도 같은 맥락을 이어갈 수 있습니다. 중요한 수명주기 전환과 고위험 판단은 사람의 명시적 결정을 요구합니다. 라우팅 규칙과 Inception부터 Learn까지의 수명주기 정의는 [AI-DLC Workflow](docs/ai-dlc/workflow.md)와 [Unit 계약](docs/ai-dlc/unit.md)에 있습니다.

한 Unit에서 발견한 용어·관례·지침·판단이 후속 Unit에도 필요하면 `Project Knowledge` candidate로 제안하고 사람의 Knowledge Decision 뒤 승격합니다. 이는 온톨로지나 정책 계층이 아니라 승인된 프로젝트 공통 지식이며, 새 Unit은 생성 시점 release digest와 자기 `work_scope`에 겹치는 활성 항목만 Context Receipt에 고정합니다. 기존 Unit은 자동으로 최신 지식을 읽지 않습니다.

```text
사용자 채팅 명령
      ↓
Codex / Claude / Kiro Runtime Adapter
      ↓
프로젝트 로컬 ISEKAI Core
      ↓
Foundation + Project + Unit artifacts
```

### Runtime, Skill, Core의 차이

| 구성 | 역할 |
|---|---|
| Project Runtime | 프로젝트별로 버전이 고정되는 Core·Foundation·Runtime Skill 묶음 |
| Runtime Skill | 각 호스트의 repo/project/workspace 검색 위치에 설치되어 호출 방식·라우팅·안전 규칙을 제공하는 얇은 Adapter |
| Core | workflow·호환성·Decision·Evidence를 검증하고 Unit 문서와 Project 변경을 원자적 gateway action으로 적용하는 로컬 실행기 |

Skill이 상태의 원본은 아닙니다. `project.json`, `isekai.lock.json`, Foundation과 Unit artifact가 권위 있는 상태이며 Adapter는 Core와 handshake한 뒤 이를 사용합니다.

ISEKAI Catalog는 이세카이가 제공하는 기능을 하나의 공통 목록과 계약으로 묶습니다. 각 Catalog entry는 Project-local Core MCP 통제면에 등록되며 ID·버전·상태·action·resource·digest로 식별됩니다. 현재 Catalog에는 실행 가능한 AI-DLC가 등록돼 있고, 향후 추가 기능은 각각 독립된 ISEKAI Catalog entry package로 배포해 같은 Catalog에 등록합니다. 자세한 계약은 [ISEKAI Catalog](docs/catalog.md)를 참고하세요.

Catalog 배포 원본은 이세카이 저장소의 `catalog/catalog.json`과 `catalog/<entry-id>/<version>/`에서 관리합니다. Git release는 이 디렉터리 전체를 독립 component digest로 결박하고 설치기가 대상 프로젝트의 `.isekai/catalog/`에 배치합니다.

## 지원 런타임과 채팅 명령

| 런타임 | 통합 방식 | 대화에서 활성화 |
|---|---|---|
| Codex | Repo Skill | `$isekai on` |
| Claude Code | Project Skill | `/isekai on` |
| Kiro | Workspace Agent Skill | `/isekai on` |

모든 새 대화는 ISEKAI mode가 `off`인 상태로 시작합니다. Runtime Skill의 설치·발견·cache, repository 내용, 문장 속 명령 인용은 호출이나 활성화가 아닙니다. Mode가 꺼져 있을 때는 위 표의 Runtime별 명령을 실행하려는 명시적 호출만 one-shot으로 처리하며, `on`만 이후 요청의 자동 라우팅을 활성화합니다. `on`은 Unit을 새로 선택하지 않지만 Project context·Unit 후보와 Core가 보존한 `active_unit_binding`을 반환합니다. 결박된 unfinished Unit이 있으면 정확한 경로로 `resume`해야 합니다.

## 설치

### 요구 사항

- Git
- Python 3.11 이상

설치 스크립트는 전역 Python package를 설치하지 않습니다. 설치할 Git tag를 임시 checkout하고 release digest를 검증한 뒤 대상 프로젝트 안에 Core, Foundation과 Runtime Adapter를 배치합니다.

아래 명령은 설치하려는 프로젝트 루트에서 실행합니다. 예시는 Codex Runtime Skill과 Project를 초기화합니다. 다운로드 URL은 해당 release tag가 GitHub에 게시된 뒤 사용할 수 있습니다.

### macOS / Linux

```bash
ISEKAI_VERSION=v0.2.1
curl -fsSLo /tmp/isekai-install.sh \
  "https://raw.githubusercontent.com/dotwin7/isekai/${ISEKAI_VERSION}/scripts/install.sh"
bash /tmp/isekai-install.sh \
  --source https://github.com/dotwin7/isekai.git \
  --ref "$ISEKAI_VERSION" \
  --path . \
  --runtime codex \
  --init \
  --maximum-agent-level L1 \
  --profile software-delivery-profile
./.isekai/bin/isekai doctor --path .
```

### Windows PowerShell

```powershell
$IsekaiVersion = "v0.2.1"
$installer = Join-Path ([IO.Path]::GetTempPath()) "isekai-install-$IsekaiVersion.ps1"
Invoke-WebRequest `
  "https://raw.githubusercontent.com/dotwin7/isekai/$IsekaiVersion/scripts/install.ps1" `
  -OutFile $installer
& $installer `
  -Source https://github.com/dotwin7/isekai.git `
  -Ref $IsekaiVersion `
  -Path . `
  -Runtime codex `
  -Init `
  -MaximumAgentLevel L1 `
  -Profile software-delivery-profile
py -3 .\.isekai\bin\isekai.py doctor --path .
```

### 런타임 선택과 프로젝트 Skill 설치

| 설치 대상 | 옵션 | 설치 경로 |
|---|---|---|
| Codex | `--runtime codex` | `.agents/skills/isekai/` |
| Claude Code | `--runtime claude` | `.claude/skills/isekai/` |
| Kiro | `--runtime kiro` | `.kiro/skills/isekai/` |
| 세 런타임 모두 | `--runtime all` | 위 세 프로젝트 경로 |

`--runtime`은 반복해서 지정할 수 있습니다. 설치 스크립트는 선택한 Runtime Skill과 Core를 배치한 뒤 해당 Project의 실행 보호 설정을 적용하고 Project-local Core MCP gateway까지 자동 연결합니다. 사용자 홈과 host marketplace는 변경하지 않으며 lifecycle 훅도 설치하지 않습니다. 기존 Project host 설정의 원본 bytes는 `.isekai/host-custody/state.json`에 보존됩니다.

```bash
# 정상 설치 뒤에는 필요하지 않습니다. 문제가 의심될 때만 사용합니다.
./.isekai/bin/isekai doctor --path .
./.isekai/bin/isekai doctor --path . --fix
```

Claude는 `--runtime claude`, Kiro는 `--runtime kiro`를 사용합니다. 설치 명령 승인이 선택 Runtime의 Project-local 실행 보호 설정까지 포함합니다. 설치가 끝나면 새 Agent CLI 세션을 시작하고 `$isekai on` 또는 `/isekai on`으로 제어합니다. Kiro에서는 생성된 `isekai-core` agent를 선택한 세션을 사용합니다. `doctor --fix`는 설치된 Runtime을 lock에서 자동으로 찾아 보호 설정을 복구하므로 `--runtime`을 따로 지정하지 않습니다. `handshake`는 Project-local read-only 선언과 Core gateway 연결을 확인하지 못하면 fail-closed합니다. 실제 Host process의 더 높은 우선순위 flag나 managed policy는 Host가 통제하므로, 그런 override로 sandbox를 다시 여는 것은 운영자 우회입니다. 각 프로젝트는 `isekai.lock.json`으로 Core와 Skill 버전을 독립적으로 고정합니다.

기존 `project.json`이 없다면 `--init`이 manifest와 `units/`를 생성합니다. 수동으로 초기화하려면 설치된 launcher를 사용합니다.

```bash
./.isekai/bin/isekai init \
  --path . \
  --maximum-agent-level L1 \
  --profile software-delivery-profile
```

`maximum_agent_level`의 기본값 `L0`은 승인된 Envelope 안에서도 읽기만 허용합니다. 로컬 파일 편집과 테스트에는 `L1`, 정확히 allowlist된 개발·테스트 외부 API까지 필요하면 `L2`를 명시합니다. L2도 API key 원문을 Agent에 주는 권한이 아니며 `credential-access`, Production, 배포, 원격 Git, 고객 데이터는 계속 금지됩니다. 키는 호스트 secret store에 두고 Envelope에는 `secret://provider/name` 참조만 기록합니다. 보안 개발 규칙도 함께 사용할 프로젝트는 `--profile security-profile`을 추가할 수 있습니다. 이미 존재하는 `project.json`은 자동으로 덮어쓰지 않습니다.

### 설치 결과

선택한 런타임에 따라 다음 파일이 프로젝트에 생성됩니다.

```text
project/
├── .isekai/
│   ├── bin/                         # 프로젝트 로컬 launcher
│   ├── runtime/isekai/              # ISEKAI Core
│   ├── catalog/                     # ISEKAI Catalog과 versioned entry package
│   └── foundations/<version>/       # 고정된 Foundation
├── .agents/skills/isekai/            # Codex repo Skill
├── .claude/skills/isekai/            # Claude project Skill
├── .kiro/skills/isekai/             # Kiro를 선택한 경우
├── isekai.lock.json                 # Git ref, commit, component digest
├── project.json                     # Project 계약
└── units/                           # 지속되는 AI-DLC 작업 단위
```

설치기는 bootstrap, Core, host-neutral Runtime contract, Catalog, Foundation과 Adapter의 파일 경로·bytes·실행 비트를 포함한 SHA-256 tree digest를 검증하고 component의 symlink·hardlink·특수 파일을 거부합니다. 공통 Runtime component에는 manifest·호환성 Evidence·Runtime Skill 생성 원본도 포함됩니다. `project.json`, `isekai.lock.json`과 배포 control manifest도 single-link regular file로만 읽고 lock의 필드 타입과 digest 형식을 사용 전에 검증합니다. 또한 Git revision 표현이 아닌 canonical tag 또는 전체 commit만 받고, checkout의 origin·immutable ref·HEAD·clean worktree와 일치하는 Git commit만 `isekai.lock.json`에 고정합니다. update가 만드는 rollback snapshot 전체는 새 lock의 digest에 결박되며, rollback은 이를 확인하고 redo snapshot을 다시 결박한 뒤에만 복원합니다. 이전 marketplace 기반 0.1.0 설치를 업데이트하면 ISEKAI가 소유한 legacy 선언만 제거하고 다른 host 설정은 보존합니다.

## 사용법

에이전트를 `project.json`이 있는 저장소에서 시작한 뒤 해당 호스트의 명령을 사용합니다.

### Codex

```text
$isekai on
$isekai status
$isekai resume --unit units/<unit-id>
$isekai unit-migrate --project . --unit units/<unit-id>
$isekai off
```

### Claude Code

```text
/isekai on
/isekai status
/isekai resume --unit units/<unit-id>
/isekai unit-migrate --project . --unit units/<unit-id>
/isekai off
```

### Kiro

```text
/isekai on
/isekai status
/isekai resume --unit units/<unit-id>
/isekai unit-migrate --project . --unit units/<unit-id>
/isekai off
```

`on` 상태에서 Core active Unit 결박이 없을 때만 이후 요청이 `intake`를 거쳐 Query, Quick Change 또는 Unit으로 라우팅됩니다. `unit-init`이나 `resume`은 unfinished Unit 하나를 Project에 결박하며, `learned` 전에는 Core가 새 라우팅·새 Unit·형제 Unit의 persistent action을 거부하고 `amend`를 요구합니다. `off`는 자동 라우팅만 중단하며 Unit, Decision, Evidence, Checkpoint 또는 active Unit 결박을 수정하거나 삭제하지 않습니다. 명시적인 action은 mode가 꺼져 있어도 one-shot으로 실행할 수 있지만 같은 Core 경계를 따릅니다.

Runtime Adapter는 `intake`를 호출할 때 전체 대화 맥락에서 변경 크기와 `risk`·`ambiguous`·`multi_party`·`remote`·`sensitive` 신호를 판정해 전달합니다. Core도 직접 호출이나 누락에 대비해 요청 문장의 명백한 운영 환경·자격증명·고객 데이터·고위험 실행 신호를 보수적으로 추론합니다. Core가 감지한 신호는 명시적인 저위험 값으로 낮출 수 없으며 `intent.classification.inferred_signals`에 남습니다. 이 텍스트 추론은 Adapter 판단의 대체물이 아니라 방어 계층입니다.

`intake`는 active Unit이 없을 때 Route와 함께 `direct-response`, `bounded-change`, `adaptive-unit` 중 하나의 Workflow Directive를 반환합니다. Unit이면 Agent가 프로젝트를 먼저 읽기 전용으로 탐색하고 단계별 적용 여부와 깊이가 포함된 Level-1 plan을 제안합니다. 사용자가 전체 계획을 승인한 뒤 Unit을 만들며, 승인 범위의 로컬 기록과 기계적 상태 전이마다 확인을 반복하지 않습니다. `status`와 `resume`의 `human_gate`가 다음에 필요한 Inception·Architecture·Release·Operation Decision과 차단 여부를 알려 줍니다. Gate 결과를 명시적으로 거부하면 `rejected`, 거부가 아닌 수정·추가 요구는 같은 Unit의 `amend`로 기록하고 수정·재검증 후 새 packet으로 승인을 다시 요청합니다. 중요한 Decision이나 범위·위험·외부 효과의 확대에도 다시 사람의 판단이 필요합니다.

Project 경로를 생략하면 Core는 현재 디렉터리, 가장 가까운 상위 디렉터리, 단일 하위 workspace 순서로 `project.json`을 찾습니다. 후보가 여러 개면 자동 선택하지 않고 사용자에게 경로 선택을 요구합니다.

## Unit lifecycle

지속적인 변경은 다음 lifecycle을 따릅니다.

```text
Inception → Human Decision → Construction → Validation
→ Release → Operations → Learn
```

- Inception에서는 Intent, Scope, Requirements, Acceptance Criteria와 Risk를 정리합니다.
- Construction에서는 승인된 범위 안에서 설계와 구현을 수행합니다.
- Validation에서는 독립된 lifecycle 상태와 stage authorization 아래 테스트를 실행하고 Evidence를 만듭니다.
- Release와 Operations에서는 사람이 배포·롤백·고위험 결정을 승인하고 결과를 다음 Unit에 반영합니다.
- Execution Envelope 밖의 action, Project의 `maximum_agent_level`, canonical Project scope 또는 실제 Unit stage를 벗어난 action은 Core가 거부합니다. 승인 grant는 Unit ledger에 기록되고 iteration 예산을 소모합니다.

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

### 0.1.x에서 0.2.1으로 업그레이드

대상 프로젝트 루트에서 실행합니다. 먼저 진행 중인 작업과 프로젝트 로컬 ISEKAI 파일의 변경을 커밋하거나 별도로 백업하고, 현재 설치가 정상인지 확인합니다. `update --check`는 파일을 바꾸지 않고 target commit과 component 차이만 보여 줍니다.

```bash
./.isekai/bin/isekai doctor --path .
./.isekai/bin/isekai update --check --ref v0.2.1 --path .
./.isekai/bin/isekai update --ref v0.2.1 --path .
./.isekai/bin/isekai doctor --path .
```

Windows PowerShell에서는 같은 프로젝트에서 `py -3 .\.isekai\bin\isekai.py` 뒤에 동일한 `doctor`·`update` 인자를 사용합니다. 업그레이드가 끝나면 변경된 `isekai.lock.json`, `.isekai/`, 선택한 Runtime Skill과 host 설정 diff를 검토해 함께 커밋하고, 호스트가 새 Skill 계약을 읽도록 새 대화를 시작합니다.

일반 update는 Core와 Adapter만 갱신하고 Project가 고정한 Foundation은 유지합니다. 0.2.1 Foundation의 L2 계약까지 채택하려면 진행 중 Unit이 이전 Foundation에 고정되어 있지 않은지 먼저 확인하고, `--check` 결과와 Foundation diff를 사람에게 검수받은 다음 명시적으로 적용합니다.

```bash
./.isekai/bin/isekai update --check --ref v0.2.1 --path . \
  --include-foundation --adopt-foundation
./.isekai/bin/isekai update --ref v0.2.1 --path . \
  --include-foundation --adopt-foundation
./.isekai/bin/isekai doctor --path .
```

업그레이드는 `project.json`의 `maximum_agent_level`을 자동으로 높이지 않습니다. L2가 필요한 Project만 별도 검수로 값을 `L2`로 변경하며, 기존 Unit은 생성 시 Context Receipt에 고정된 level을 유지하고 새 Unit부터 변경된 상한을 사용합니다. API key 원문은 Project나 Unit에 기록하지 않습니다.

문제가 생기면 update가 무결성 결박해 둔 직전 project-local 설치로 되돌릴 수 있습니다.

```bash
./.isekai/bin/isekai rollback --path .
./.isekai/bin/isekai doctor --path .
```

전체 설치·업데이트 계약과 실패 조건은 [설치와 업그레이드 문서](docs/installation.md)를 참고하세요.

## 안전 경계

- 모든 새 대화는 ISEKAI mode가 `off`입니다.
- Adapter version, Core version, protocol과 project lock이 맞지 않으면 handshake가 fail-closed합니다.
- Project 실행 보호 설정이 Agent의 직접 쓰기를 막고 Core MCP gateway를 연결하지 않으면 handshake가 fail-closed합니다. 이 경계에는 lifecycle 훅을 사용하지 않습니다.
- `catalog-status`와 MCP Catalog resource는 설치된 ISEKAI Catalog entry와 상태를 보여 주지만 `preview` entry의 실행이나 추가 권한을 자동 승인하지 않습니다.
- 현재 Adapter 계약에는 자율적인 high-risk action이 없습니다.
- `L0` Project는 `read`만, `L1`은 승인된 `read`·`edit`·`test`, `L2`는 여기에 정확히 제한된 개발·테스트 `external-api`만 추가합니다.
- 쓰기 action과 lifecycle Decision은 명시적 사용자 의도 또는 사람 승인을 요구합니다.
- 고객 데이터나 민감한 원본 Evidence는 Git에서 제외되는 `units/**/evidence/raw/` 아래에 둡니다.
- 일반 update는 Foundation을 자동으로 교체하지 않습니다.

### Core가 강제하는 것과 강제하지 않는 것

Core는 Decision·Envelope·Evidence의 **일관성**과 ISEKAI action의 쓰기 경계를 강제합니다. 호스트에는 독립적인 `edit`·`test` grant를 반환하지 않습니다. Unit 문서는 `artifact-write`, Project 파일은 `managed-edit`, 테스트는 `managed-test`로 요청하며 Core가 Envelope·현재 stage·active Unit·amendment·optimistic digest를 확인한 뒤 실행 결과와 digest를 같은 authorization 원장에 기록합니다. 각 Decision은 해당 lifecycle gate에서만 기록할 수 있고, Release Decision은 현재 passing Evidence의 ID와 digest를 결박합니다. Envelope는 승인 시점의 digest로 Inception Decision에 결박되고, 승인 뒤 내용이 바뀌면 실행과 verify가 거부됩니다. 미완료 acceptance·artifact·checkpoint가 남으면 `releasing` 또는 `learned` 전이도 거부합니다.

Envelope는 active Unit 하나에만 속합니다. `scope: ["**"]`가 승인되어도 Core는 기본 `units/` collection과 형제 Unit artifact에 대한 `read`·`edit`·`test` authorization을 거부하며, 사용자 지정 Project-local Unit 경로에도 같은 경계를 적용합니다. 이 경계는 프로젝트 소스·테스트·고정 Foundation·Profile·Extension 접근을 막는 filesystem sandbox가 아니라 Decision·Checkpoint·Evidence가 Unit 사이에서 섞이지 않게 하는 실행 격리입니다.

공통화할 결과는 형제 Unit을 직접 읽게 하지 않고 `project-knowledge-propose → decision --gate knowledge → project-knowledge-promote`로 승격합니다. `project-knowledge/`는 Core-managed path이며 active Unit은 catalog를 직접 읽지 않고 자기 Receipt에 scope별로 고정된 항목만 사용합니다. Unit이 선택되지 않은 Project context에는 release 요약만 들어가며, `project-knowledge-status`는 candidate를 승인 대기·승인·거부·stale·승격·invalid로 구분하고 schema 호환 상태도 보여 줍니다. 자세한 계약은 [Project Knowledge](docs/project-knowledge.md)를 참고하세요.

`managed-test`는 원본 Project가 아닌 일회용 복제 workspace에서 최소 허용 환경 변수만 넘기고 OS sandbox 안에서 명령을 실행합니다. 복제는 directory descriptor와 `O_NOFOLLOW`를 사용해 각 항목의 inode·metadata를 복사 전후에 확인하며 symlink·hardlink·특수 파일과 복사 중 변경을 거부합니다. macOS Seatbelt와 Linux Bubblewrap provider는 원본 Project와 사용자 홈의 파일 내용을 읽지 못하게 하고(runtime executable root는 read-only 예외), 쓰기를 일회용 workspace로 제한하며 네트워크를 차단합니다. Linux는 PID namespace를 사용하고 macOS는 외부 process signal/info와 Mach service 접근을 차단하며, Core는 종료 시 같은 process group의 자식을 정리합니다. 명령에는 CPU·생성 파일 크기·open file·process 수·core dump hard limit를 적용하고 Linux에서는 4 GiB address-space limit도 적용합니다. provider가 없거나 preflight에 실패하면 명령을 실행하지 않고 fail-closed합니다. Windows의 로컬 `managed-test`는 현재 provider가 없어 거부되므로 보호된 CI/원격 sandbox가 만든 Evidence를 사용해야 합니다. stdout/stderr는 메모리 pipe에서 합계 8 MiB까지만 수집하고 초과 시 명령을 종료하며, authorization receipt에는 provider·격리/자원 제한·exit code·수집된 출력 digest와 byte count가 결박됩니다. 호출자에게는 stream별 최대 256 KiB만 반환합니다. 별도 CI나 외부 runner가 제출한 Evidence도 계속 허용되고 `attestation.output_digest_verification`은 Core가 원문 output에서 digest를 계산했는지 또는 caller가 digest만 제출했는지를 보존합니다.

Core는 Host 대화 원문을 독립적으로 관찰하지 않으므로, active Unit 중 새 사용자 변경을 `amend`로 보고하는 행위는 Runtime Adapter가 담당합니다. 정상 Adapter가 새 요청을 `intake`로 보내면 Core는 새 route를 거부하고 `active-unit-amendment-required`를 반환하며, 기록된 Amendment 뒤에는 영향 문서·새 Decision·Checkpoint를 강제합니다. 그러나 Agent가 사용자 메시지를 아예 보고하지 않았다는 사실까지 MCP 서버가 감지할 수는 없습니다. lifecycle 훅이나 Host 플러그인 없이 대화 interception을 하지 않는 선택의 명시적 trust boundary입니다.

Core는 `--decided-by`에 적힌 주체가 실제 사람인지는 **검증하지 않습니다**. Decision은 Core를 호출할 수 있는 누구나 기록할 수 있는 로컬 JSON 레코드이므로, 셸 접근 권한을 가진 에이전트는 자기 Envelope를 스스로 승인할 수 있습니다. 새 Decision은 `attestation.identity_verification: not-performed-by-core`와 caller가 보고한 actor를 digest에 함께 결박해 이 경계를 숨기지 않습니다. 호스트의 도구 실행 승인도 특정 shell·파일 action에 대한 권한일 뿐 lifecycle Decision이 아닙니다. 사람의 판단은 완성된 Decision Packet을 보여 주는 인증된 대화 UI나 외부 승인 시스템에서 받고, ISEKAI에는 그 결과를 감사 가능한 레코드로 남깁니다. 이 경계를 넘는 강제가 필요하면 원격 IAM, 보호 브랜치, 승인 시스템 같은 Core 외부 통제를 함께 사용하세요.

Runtime manifest와 `isekai runtime compatibility` 응답의 `human_decision_actions`·`trust_model`이 이 경계를 기계가 읽을 수 있게 표시합니다. Adapter는 이 목록의 action을 호출하기 전에 사용자에게 실제 확인을 받아야 합니다.

```text
amend  active-unit-detach  decision  foundation-decision  foundation-promote
```

`envelope-approve`와 `transition`은 이미 기록된 Decision과 승인된 계획을 반영하는 기계적 상태 변경이므로 별도의 인간 판단 action으로 분류하지 않습니다. 다만 승인된 범위·위험·외부 효과나 단계 계획이 달라지면 새 Decision이 먼저 필요합니다.

Unit과 Foundation 원장은 read-modify-write 문서이므로, 모든 변경은 Unit·Foundation 단위 운영체제 file lock으로 직렬화됩니다. Unit의 `verify`, `status`, `resume`도 같은 락 아래에서 여러 artifact의 일관된 snapshot을 읽습니다. 다른 프로세스가 쓰는 중이면 짧게 대기하고, 그래도 잡히지 않으면 조용히 덮어쓰거나 traceback을 노출하는 대신 구조화된 오류로 실패합니다. 프로세스가 비정상 종료되면 운영체제가 lock을 해제하므로 방치 파일을 시간 기준으로 경쟁적으로 회수하지 않습니다.

릴리스 digest 검증도 같은 성격입니다. `distribution/release.json`은 태그 안의 component 경로·bytes·실행 비트와 source manifest에서 유도한 ID·version·path metadata가 서로 일치하는지 확인하며, 서명 검증이 아니므로 신뢰 기준점은 지정한 Git 원격과 immutable tag입니다.

## 호환성 기준

`tested_versions`는 최소 요구 버전이 아니라 현재 연결된 관찰 근거가 있는 live 기준입니다. 과거 문서에만 남고 원시 근거가 연결되지 않은 주장은 `legacy_versions`로 분리했습니다. 호스트 CLI를 올린 뒤에는 Adapter 구조 검증과 Core의 `status`, `resume`, `verify` smoke를 다시 수행해야 합니다.

| 런타임 | Live 검증 기준 | 별도 상태 | 통합 surface |
|---|---:|---|---|
| Codex | `0.147.0` | legacy `0.146.0` | Repo Skill |
| Claude Code | 없음 | validation-only `2.1.224`, legacy `2.1.220` | Project Skill |
| Kiro | 없음 | validation-only `2.16.2`, legacy `2.14.2` | Workspace Agent Skill |

## 저장소 구조

```text
isekai/
├── distribution/           # release manifest와 component digest
├── foundation/             # Core 계약, Profile, Policy, Evaluation
├── runtime/                # host-neutral Runtime 계약과 프로젝트 Skill 원본
├── scripts/                # POSIX / PowerShell bootstrap installer
├── src/isekai/             # 로컬 Core와 CLI
├── tests/                  # 계약, lifecycle, 설치·업데이트 테스트
├── docs/                   # canonical 설계 문서 집합 (isekai.md가 입구)
└── project.json            # 이 저장소 자체의 Project 계약
```

## 개발과 검증

```bash
uv sync --extra test
uv run pytest
uv run python scripts/generate-runtime-skills.py --check
uv run python scripts/runtime-host-check.py --runtime all
uv run python -m isekai distribution-check --root .
uv run python scripts/live-smoke.py
```

설치된 CLI 계약은 `--runtime claude --require-cli` 또는 `--runtime kiro --require-cli`로 검사합니다. 실제 모델 호스트 호출은 비용·인증이 필요하므로 명시적으로 선택합니다: `uv run python scripts/live-smoke.py --runtime codex --host codex`. 검증 범위와 최근 관찰 결과는 [Runtime live smoke](docs/live-smoke.md)에 기록합니다.

배포 component가 변경되면 tag를 만들기 전에 manifest를 다시 생성하고 검증합니다.

```bash
uv run python -m isekai distribution-build --root .
uv run python -m isekai distribution-check --root .
```

세부 workflow, Foundation 계약, artifact schema와 운영 정책은 [canonical 설계 문서 집합](docs/isekai.md)에서 시작하세요. 개요와 문서 맵은 `docs/isekai.md`에 있고, 주제별 세부 계약은 architecture·installation·workflow·unit·project-knowledge·foundation·information-model·agent-integration·roadmap 문서가 나눠 소유합니다. Runtime별 세부사항은 [Codex](runtime/adapters/codex/README.md), [Claude Code](runtime/adapters/claude/README.md), [Kiro](runtime/adapters/kiro/README.md) Adapter 문서에 있습니다.

프로젝트 로컬 설치부터 실제 제품 기능과 `learned` Unit까지의 결정론적 Golden Path는 [Reference Product](examples/reference-product/README.md)와 `tests/test_reference_product_e2e.py`에서 확인할 수 있습니다. 이 테스트는 설치된 launcher 계약을 사용하며 실제 호스트 모델 세션은 [Runtime live smoke](docs/live-smoke.md)로 분리합니다.
