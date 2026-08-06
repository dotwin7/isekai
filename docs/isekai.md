# ISEKAI

- 상태: Canonical
- 작성일: 2026-08-03
- 설명: 범용 AI-DLC 운영 모델 및 Security Engineering Domain Profile
- 문서 역할: 이 저장소의 유일한 canonical 설계 문서

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

Agent Integration은 사용자가 별도의 Python 모듈 명령을 실행하는 구조가 아니다. 사용자는 Kiro·Claude·Codex에서 `/isekai`를 호출하고, Runtime Adapter가 설치된 `isekai` launcher를 내부적으로 호출한다. launcher는 같은 로컬 환경의 ISEKAI Core dispatch를 실행하고, Core는 프로젝트가 선택한 Foundation·Profile·Extension과 Unit artifact를 읽고 검증한다. 직접 CLI를 사용하는 경우에는 `isekai <action>`을 사용하며, `isekai plugin <action>`은 Runtime Adapter의 내부 호환 계약이다.

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

### 4.1 Project bootstrap과 discovery

ISEKAI 적용 저장소는 기본적으로 루트에 `project.json`을 둔다. Agent CLI를 저장소 루트나 하위 디렉터리에서 실행하면 별도 경로 없이 Project를 선택할 수 있다.

```text
project-root/
├─ project.json
├─ foundation/ 또는 project.json의 foundation_path
├─ units/
├─ src/
└─ tests/
```

Project가 없으면 명시적 사용자 확인 후 `isekai init --path PATH`로 manifest와 `units/`를 생성한다. Init은 Foundation·Profile을 preflight하고 기존 `project.json`을 덮어쓰지 않으며 실패한 manifest를 rollback한다.

Project discovery 순서는 direct current directory → nearest ancestor → filtered descendants다. 중첩 Project에서는 가장 가까운 ancestor manifest를 사용한다. descendant 후보가 하나면 선택할 수 있지만 둘 이상이면 모든 후보를 표시하고 `--project`로 명시적 선택을 요구한다. `.git`, build output, dependency, runtime과 `units/` 디렉터리는 descendant 검색에서 제외한다.

`project.json.parent`가 Project root다. `unit-init`의 output을 생략하면 `project-root/units/`를 사용한다. 상대 output은 Project root 기준이며 `..` 또는 symlink로 root를 벗어나면 거부한다. 명시적 절대 output은 외부 저장 의도로 간주해 허용한다. `foundation_path`는 조직 공통 Foundation을 공유할 수 있도록 Project 외부 상대·절대 경로를 허용한다. Unit metadata, Decision, Receipt, Checkpoint와 검증 Evidence는 Stage 1의 공유 Persistent Context로 버전 관리한다. 고객 데이터나 민감한 원본 출력은 `units/**/evidence/raw/` 아래에 두고 Git에서 제외한다.

### 4.2 Git release 설치와 프로젝트 버전 고정

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

```bash
./.isekai/bin/isekai update --check --ref v0.2.0 --path .
./.isekai/bin/isekai update --ref v0.2.0 --path .
./.isekai/bin/isekai rollback --path .
```

`update --check`는 target commit과 component 변경을 읽기 전용으로 보고한다. 일반 update는 Core와 Adapter만 갱신하고 Project가 고정한 Foundation은 유지한다. Foundation 변경은 diff와 사람 승인을 거쳐 `--include-foundation`을 명시해야 하며, 기존 외부 Foundation과 다르면 `--adopt-foundation`도 요구한다. 진행 중 Unit은 생성 당시 Foundation version과 contract digest를 유지하고, 현재 Project 계약과 다르면 명시적 migration 전까지 resume을 차단한다.

## 5. 작업 라우팅

모든 질문과 변경에 정식 AI-DLC를 적용하지 않는다. 작업의 지속성·위험·불확실성·협업 필요성에 따라 세 경로로 나눈다.

### 5.1 Query

설명·조회·요약·읽기 전용 분석·아이디어 비교는 바로 처리한다.

```text
질문 → 필요한 정보 확인 → 답변
```

별도 Unit과 영속 산출물은 만들지 않는다.

### 5.2 Quick Change

작고 명확하며 저위험이고 쉽게 되돌릴 수 있는 변경이다.

```text
의도 확인 → 최소 변경 → 관련 검증 → 결과 기록
```

오타, 작은 문서 수정, 단일 파일의 명백한 버그와 기존 동작을 바꾸지 않는 정리가 해당한다. 정식 Inception 문서는 만들지 않지만 변경과 검증 결과는 남긴다.

### 5.3 Unit of Work

제품·계약·운영에 지속적인 영향을 주거나 여러 사람이 판단해야 하는 변경이다.

- 요구사항이나 완료 조건이 모호함
- 제품 동작·사용자 경험·여러 컴포넌트에 영향
- API·데이터·Semantic·Knowledge 계약 변경
- 에이전트 행동·권한·평가 변경
- 운영 배포·원격 변경·고객 Scope·비밀정보 관련
- 여러 세션·사람·에이전트가 협업
- 결정과 근거를 장기간 보존해야 함

```text
Inception → Human Decision → Construction → Validation
→ Release → Operations → Learn
```

크기가 작아도 프로덕션, 권한, 고객 Scope, 비밀정보, 데이터 삭제, 인프라와 고위험 에이전트 실행이 포함되면 Unit으로 승격한다.

### 5.4 Goal/Direct Request Intake

Host의 `/goal` 결과와 사용자의 직접 요청은 별도 lifecycle로 만들지 않고 동일한 Normalized Intent로 변환한다.

```text
/goal 또는 직접 요청
→ Goal, Expected Outcome, Scope, Constraints, Acceptance Criteria 정규화
→ Query / Quick Change / Unit 라우팅
```

Query는 Unit을 만들지 않고 답변한다. Quick Change는 최소 변경과 검증만 남긴다. Unit은 정규화된 Intent를 `unit.json`과 `intent.md`에 보존하고 Inception부터 AI-DLC를 시작한다. Goal은 별도 Agent나 Goal Engine이 아니라 AI-DLC의 입력 방식이다.

## 6. AWS형 AI-DLC

AWS AI-DLC의 핵심 모델을 유지한다.

```text
AI가 계획·질문·선택지를 제시
→ 사람이 의도·범위·중요 결정을 검증
→ AI가 승인된 계획을 실행
→ 자동 평가와 사람이 결과를 검토
→ 산출물·결정·증거를 다음 단계에 영속화
```

### 6.1 Inception

사업·보안 의도를 검증 가능한 Unit으로 바꾼다.

AI는 자료를 탐색하고 명확화 질문, 요구사항, 비목표, 위험과 인수 조건을 제안한다. 사람은 문제, 기대 성과, 범위, 우선순위, 제약과 Construction 진입을 결정한다.

필수 결과는 Intent, Requirements, Decisions, Acceptance Criteria와 Risk다.

### 6.2 Construction

승인된 의도를 제품 변화로 만든다.

AI는 아키텍처·도메인·Semantic 변경, 구현 계획, 코드, 테스트, 문서와 평가를 제안·작성한다. 사람은 중요한 아키텍처, 외부 계약, 권한, 마이그레이션과 Release 진입을 결정한다.

필수 결과는 Architecture, Plan, Code, Tests, Evaluations, Evidence와 Release Decision이다.

### 6.3 Operations

안전하게 배포하고 실제 결과를 다음 Unit에 반영한다.

AI는 배포·운영 절차, 관측 결과, 장애·사용자 피드백과 개선 Unit을 제안한다. 사람은 운영 배포, 롤백, 고위험 실행, 사고 대응과 공식 지식 승격을 결정한다.

필수 결과는 Release Evidence, Deployment Record, Operational Feedback, Incident/Lesson과 Next Unit이다.

### 6.4 공통 반복 루프

```text
Plan → Clarify → Human Decision → Execute → Verify → Persist
```

AI는 승인되지 않은 중요한 결정을 암묵적으로 대신하지 않는다.

## 7. Unit과 인간 게이트

Unit은 의도에서 운영 결과까지 추적하는 정식 AI-DLC 작업 단위다.

```text
unit/
├─ unit.json
├─ intent.md
├─ requirements.md
├─ decisions.json
├─ architecture.md
├─ implementation-guide.md
├─ plan.md
├─ acceptance.md
├─ evaluations/criteria.json
├─ evidence/verification.json
├─ checkpoint.json
├─ context-receipt.json
├─ execution-envelope.json
├─ execution-authorizations.json
├─ release.md
└─ operations.md
```

Unit 상태는 다음을 기본으로 한다.

```text
proposed
→ inception
→ awaiting-inception-decision
→ construction
→ awaiting-release-decision
→ releasing
→ operating
→ learned
```

| 게이트 | 사람이 확인할 내용 |
|---|---|
| Inception Decision | 문제·범위·인수 조건·위험 |
| Architecture Decision | 도메인·Semantic·외부 계약 |
| Release Decision | 테스트·평가·미해결 위험·롤백 |
| Operation Decision | 고위험 실행·사고·권한 변경 |
| Knowledge Promotion | 지식의 근거·중복·유효기간 |

Unit의 위험과 Profile에 따라 산출물을 줄일 수 있지만 Decision과 Evidence를 생략해 중요한 게이트를 우회할 수는 없다.

Decision은 Unit의 `decisions.json`에 다음 최소 구조로 기록한다.

```json
{
  "id": "DEC-...",
  "type": "human-decision",
  "schema_version": "1.0.0",
  "unit_id": "UNIT-...",
  "gate": "inception|architecture|release|operation|knowledge",
  "outcome": "approved|rejected",
  "summary": "사람이 승인하거나 거부한 판단의 요약",
  "scope": "project:security-product",
  "decision_packet_version": "1.0.0",
  "rationale": [
    "선택한 설계가 요구사항과 Scope에 맞는다."
  ],
  "alternatives": [
    {
      "option": "대안 설계",
      "reason": "선택하지 않은 이유"
    }
  ],
  "tradeoffs": [
    "얻는 것과 포기하는 것"
  ],
  "risks": [
    "남아 있는 위험"
  ],
  "references": [
    "requirements.md",
    "execution-envelope.json"
  ],
  "approval_subject": {
    "type": "execution-envelope",
    "id": "ENV-UNIT-...",
    "digest": "sha256:..."
  },
  "decided_by": "human-owner",
  "decided_at": "2026-08-04T00:00:00+00:00"
}
```

Core는 lifecycle을 임의로 건너뛰는 전이를 허용하지 않는다.

```text
proposed → inception → awaiting-inception-decision
→ construction → awaiting-release-decision → releasing
→ operating → learned
```

`construction` 진입에는 승인된 Inception Decision, `awaiting-release-decision` 진입에는 승인된 Architecture Decision, `releasing` 진입에는 승인된 Release Decision과 passing verification Evidence, `learned` 진입에는 승인된 Operation Decision이 필요하다. 같은 게이트의 최신 Decision이 `rejected`이면 승인으로 간주하지 않는다.

Verification Evidence는 실행 결과를 재현할 수 있도록 다음 최소 구조를 갖는다.

```json
{
  "id": "EVD-...",
  "type": "verification-evidence",
  "schema_version": "1.0.0",
  "unit_id": "UNIT-...",
  "passed": true,
  "scope": "Core and plugin Golden Path",
  "recorded_by": "validator",
  "recorded_at": "2026-08-04T00:00:00+00:00",
  "commands": [
    {
      "command": "PYTHONPATH=src python3 -m pytest -q",
      "exit_code": 0,
      "output_digest": "sha256-hex-64-characters",
      "observed_at": "2026-08-04T00:00:00+00:00"
    }
  ]
}
```

Evidence는 명령·exit code·결과 digest·관찰 시각·범위·기록 주체를 보존해야 한다. Release Decision만 있고 passing Evidence가 없으면 `releasing` 전이를 허용하지 않는다.

Agent 실행은 Unit별 Execution Envelope로 제한한다. Agent는 Context와 Intent를 바탕으로 Envelope를 제안할 수 있지만, 사람의 Inception Decision이 `execution-envelope.json`을 참조해 승인하기 전에는 Construction을 시작할 수 없다.

```json
{
  "id": "ENV-UNIT-...",
  "type": "execution-envelope",
  "schema_version": "1.0.0",
  "unit_id": "UNIT-...",
  "status": "approved",
  "scope": ["src/**", "tests/**"],
  "stages": [
    {
      "name": "construction",
      "depth": "standard",
      "allowed_actions": ["read", "edit", "test"]
    }
  ],
  "allowed_actions": ["read", "edit", "test"],
  "forbidden_actions": ["remote", "deploy", "credential-access"],
  "max_iterations": 5,
  "approval_digest": "sha256:...",
  "approval_decision_id": "DEC-..."
}
```

Inception Decision은 Envelope의 고유 ID와 `approval_digest`를 함께 결박한다. 이후 Envelope가 교체되거나 변경되면 다시 사람의 승인을 받아야 한다. `authorize`는 Project 내부의 정규화된 target과 실제 Unit phase만 사용하고, 허용된 grant를 `execution-authorizations.json`에 기록하면서 `max_iterations` 예산을 소모한다.

### Envelope 갱신

`expires_at`은 승인이 새 action을 허가하는 창을 한정한다. 기본 창은 168시간이며 `--expires-in-hours`로 최대 720시간까지 조정한다. 창이 닫히거나 `max_iterations` 예산이 소진되면 Unit을 폐기하지 않고 Envelope를 갱신한다.

```text
envelope-propose --unit PATH ...      # 교체 Envelope를 proposed 상태로 기록하고 ledger를 초기화
decision --gate inception --outcome approved --reference execution-envelope.json ...
envelope-approve --unit PATH          # 새 Decision에 결박해 활성화
```

교체 Envelope는 `proposed` 상태로 시작하므로, 새 Decision이 승인하기 전까지 Unit은 어떤 authorization도 보유하지 않는다. 즉 갱신은 만료를 우회하는 경로가 아니라 사람의 승인을 다시 요구하는 경로다. 만료는 authorization 시점에만 판정하며, `verify`는 Envelope의 구조와 결박만 감사하므로 승인 창이 닫힌 뒤에도 Unit 기록은 계속 검증 가능하다.

### 원장 동시성

`decisions.json`과 `unit.json`은 read-modify-write 원장이다. 여러 세션·런타임이 같은 Unit을 다룰 수 있으므로, Unit의 모든 변경(Decision, transition, Envelope 제안·승인, Evidence, checkpoint, authorization)은 Unit 단위 파일 락으로 직렬화한다. Foundation release Decision도 같은 방식으로 Foundation 단위 락을 사용한다.

락은 `os.link`의 원자성으로 획득하고 inode 비교로 소유를 확인한다. 이 확인이 없으면 두 프로세스가 같은 방치 락을 동시에 stale로 판정하고 각자 자기 락이라고 믿는 경쟁이 남는다. 락을 잡지 못하면 짧게 대기하고, 그래도 실패하면 덮어쓰지 않고 오류를 낸다. 5분이 지난 락은 프로세스가 죽은 것으로 보고 회수한다.

Decision 기록의 postflight는 "내 레코드가 마지막인가"가 아니라 "이전 레코드가 모두 보존된 채 내 레코드가 추가되었는가"를 확인한다. 전자는 남의 레코드를 덮어쓴 writer도 통과시킨다.

승인된 Envelope 밖의 Scope·Action·Stage는 Core가 fail-closed로 거부한다. Envelope가 없거나 불완전하면 Agent는 제안·읽기 수준에 머물며, 원격·운영·Credential Action은 기본 금지한다. 이 구조는 고정된 workflow를 강제하기보다 Unit의 Intent·위험·복잡도에 따라 Agent가 단계와 깊이를 제안하고 사람이 승인하는 Adaptive AI-DLC를 지원한다.

## 8. Engineering Foundation과 Security Profile

Foundation은 제품마다 AI-DLC가 달라지지 않게 하는 공통 자산이다. 저장소에서는 Release Manifest를 중심으로 다음 계층을 사용한다.

```text
foundation/
├─ release.json
├─ core/
│  └─ schema.json
├─ governance/
│  ├─ gate-matrix.json
│  ├─ rules/core.json
│  ├─ policies/high-risk.json
│  └─ contracts/
│     ├─ agent-execution.json
│     ├─ human-gate.json
│     └─ exception.json
├─ domains/
│  ├─ security/
│  │  ├─ profile.json
│  │  └─ semantics/security-event.json
│  └─ software-delivery/profile.json
├─ semantics/contract.json
├─ knowledge/
│  ├─ contract.json
│  ├─ catalog.json
│  └─ software-delivery-review.md
├─ units/dod-contract.json
└─ evaluations/
   ├─ routing.json
   ├─ gate.json
   ├─ release.json
   ├─ semantic.json
   ├─ knowledge.json
   ├─ exception.json
   └─ dod.json
```

- `core/`: 모든 도메인이 공유하는 공통 모델과 metadata primitive
- `governance/`: Gate matrix, Agent 실행, 인간 Decision, Exception, 공통 규칙과 고위험 정책
- `domains/`: 도메인별 Profile과 concrete Semantic mapping
- `semantics/`: mapping version·lineage·raw reference 공통 계약
- `knowledge/`: provenance·promotion·effective/expiry lifecycle과 catalog
- `units/`: 학습 가능한 artifact와 passing Evidence를 포함한 공통 DoD
- `products/`: 소비 Project가 소유하는 Product Extension
- `evaluations/`: routing·gate·release·semantic·knowledge·exception·DoD positive/negative fixture와 실제 evaluator

Foundation의 `kind`와 `condition.type`은 closed allowlist다. `required-artifact`, `context-scope`, `extension-cannot-weaken-must`, `required-decision`, `required-envelope`, `required-lineage`, `required-promotion-review`, `required-exception-controls`, `required-dod`만 v0.1에서 허용한다. 알 수 없는 kind/condition, 불완전 provenance, rule owner/applies_to 누락, unpinned parent는 fail-closed로 거부한다.

`evaluate_foundation`은 선언된 fixture의 `valid` 값을 신뢰하지 않고 condition별 evaluator를 실행한다. routing·gate·release·semantic·knowledge·exception·DoD evaluation group이 모두 통과하고 각 group의 provenance가 Foundation release Evidence에 포함돼야 readiness와 promote를 검토할 수 있다. 자동 평가 통과는 사람의 Foundation Release Decision을 대체하지 않는다.

Foundation 자산은 `release.json`에 ID·kind·version·path로 등록하고, `extends`로 상위 계약 의존성을 표현한다. Product Extension은 Foundation Release에 등록하지 않고 소비 프로젝트가 로컬 경로로 참조한다.

예제 프로젝트는 다음처럼 Foundation과 자체 Extension을 함께 사용한다.

```text
examples/reference-product/
├─ project.json
└─ extension/
   └─ reference-product.json
```

`project.json`은 공통 Profile은 Foundation ID로 선택하고, 제품 Extension은 `{ "id": "...", "path": "..." }` 형태로 프로젝트 로컬 파일을 참조한다.

문서 언어 정책은 Project의 `document_language`로 정한다. 기본값은 `ko`이며 `intent.md`, `requirements.md`, `architecture.md`, `implementation-guide.md`, `plan.md`, `acceptance.md`, `release.md`, `operations.md`와 Decision 설명은 한국어로 생성한다. `id`, JSON key, enum, CLI 명령, 코드와 로그는 호환성을 위해 영어를 유지한다. Project가 `document_language: "en"`을 지정하면 Human-facing template만 영어로 생성한다.

규칙 계층은 다음과 같다.

```text
법·회사 보안정책
→ Foundation MUST 규칙
→ 제품·서비스 Profile
→ 저장소·컴포넌트 Extension
→ Unit별 승인된 Exception
```

하위 규칙은 상위 MUST 규칙을 완화할 수 없다. 예외에는 이유, 책임자, 보완 통제와 만료일이 필요하다. MUST 규칙은 자연어 `title`만으로 정의하지 않고 `condition.type`과 판정 필드를 함께 가져야 하며, Core가 이해하지 못하는 condition은 fail-closed로 거부한다. 적용 Context에는 rule ID가 아니라 적용 rule 전문을 포함한다.

### Foundation v0.1 완료 조건

- 규칙별 ID, MUST·SHOULD·MAY, 소유자와 적용 범위
- 개발·운영·Agent·Knowledge·Semantic 규칙
- Product Profile, Extension과 Exception 계약
- 인간 결정 게이트와 책임자
- 공통 Evaluation·Release 기준
- 자동 검사와 수동 리뷰 항목 구분
- 서로 다른 제품과 서비스에 적용 가능한 제품 중립성

Foundation v0.1은 첫 제품 Unit보다 먼저 확정한다. 실제 적용에서 발견한 빈틈은 후속 버전으로 개정한다.

### Foundation v0.1 승인 절차

Foundation 승인은 다음 두 영속 산출물을 모두 요구한다.

```text
foundation/
├─ decisions.json
└─ evidence/
   └─ release.json
```

`decisions.json`의 최신 Foundation release Decision이 `approved`여야 하며, `evidence/release.json`에는 모든 release check가 passing이어야 한다. Decision과 Evidence는 status를 제외한 release·등록 asset 전체의 `approval_digest`를 함께 기록한다. 승인이나 검증 후 Foundation 내용이 달라지면 promotion은 실패하며 새 Decision과 Evidence가 필요하다. 두 조건이 모두 충족될 때만 `foundation-promote`가 `release.json`과 모든 등록 asset의 상태를 `approved`로 승격한다. 승인 Decision이나 passing Evidence가 없으면 명령은 실패하고 Foundation 파일을 변경하지 않는다.

`release-check`는 승인 여부를 자동으로 결정하지 않고 현재 blocker를 보고한다. `foundation-promote`는 사람의 명시적 승인 이후에만 실행하는 쓰기 명령이다.

현재 공통 기준선은 `isekai-foundation@0.1.0`이며 최신 Foundation Decision `DEC-FND-20260806051711261003`과 digest-bound passing Evidence를 근거로 release와 등록된 21개 asset이 `approved` 상태다. 후속 gap은 approved v0.1.0을 임의 수정하지 않고 patch/minor Foundation version으로 보완한다. API 사용 시 `plan_foundation_promotion(root)`은 release manifest와 등록 asset 21개를 합친 22개 target의 상대 path·version·from/to status를 결정적으로 반환한다. `promote_foundation(root, dry_run=True)`는 같은 plan과 blocker만 보고하며 JSON, mode, Decision, Evidence를 변경하지 않는다.

실행 promotion은 모든 22개 JSON 결과를 메모리에서 만들고 `load_foundation` preflight와 readiness를 통과시킨 뒤 시작한다. 각 target은 같은 directory의 temporary file에 write·flush·fsync하고, 전체 staging 성공 후 `os.replace` commit한다. commit 또는 postflight(load, 22개 approved, readiness) 중 예외가 나면 원본 bytes와 mode를 복원하고 temporary file을 삭제한다. 이 transaction은 단일 프로세스·로컬 파일시스템 경계의 best-effort rollback이며 전원 손실, 파일시스템/외부 프로세스의 동시 변경, rollback 자체의 I/O 실패까지 원자성을 보장하지 않는다. descriptor의 중복·절대/상위 경로와 release.json 충돌은 preflight에서 차단한다. 기존 `promote_foundation(root)` 호출은 호환성을 위해 실행 모드로 유지한다.

## 9. 범용 Data·Semantic·Knowledge Model

정보 구조는 특정 도메인 객체를 Foundation Core에 고정하지 않는다. 범용 계약을 먼저 정의하고 도메인과 제품이 확장한다.

```text
Domain-neutral Core
        ↓
Domain Profile (Security, Software Delivery, future domains)
        ↓
Product Extension (reference product, assessment service, future products)
```

### 9.1 계층과 책임

| 계층 | 책임 | 책임이 아닌 것 |
|---|---|---|
| Operational Data | 현재 사실과 인스턴스의 원장 | 공통 의미·추론 규칙 결정 |
| Semantic Layer | 원천 필드·값·지표를 공통 의미로 매핑·노출 | 원천 사실 복제·소유 |
| Ontology | 개념 유형·허용 관계·도메인 제약 정의 | 현재 사실·권한의 원장 |
| Knowledge Layer | 검토된 설명·절차·판단 근거·경험 제공 | 실행 허용 여부 결정 |
| Policy Layer | 규범적 규칙·승인·Scope와 실행 허용 판단 | 일반 참고 지식 저장 |
| Evaluation Layer | 독립 입력·기대 결과·품질 기준 관리 | 운영 Agent에 golden label 제공 |

### 9.2 Domain-neutral Core

Core는 도메인별 이름 대신 재사용 가능한 최소 추상 계약만 제공한다.

| Core 개념 | 의미 |
|---|---|
| Entity | 식별 가능한 대상 |
| Relation | Entity 사이의 형식화된 관계 |
| Observation | 특정 시점에 관측된 사실 |
| Claim | 검토·판단이 필요한 주장 |
| Evidence | Claim·Decision을 뒷받침하는 근거 |
| Decision | 선택과 그 이유를 기록한 결과 |
| Action | 수행됐거나 제안된 행위 |
| Scope | 데이터·행위·시간의 적용 경계 |

### 9.3 Domain Profile과 Product Extension

Domain Profile은 Core를 전문화하는 용어·관계·제약·Semantic mapping 묶음이다. Product Extension은 해당 Profile을 변경하지 않고 제품 전용 필드·워크플로·지표를 namespace 아래 추가한다.

```text
Security Profile: Asset, Identity, Event, Alert, Case, Finding, Control
Software Delivery Profile: Requirement, Component, Change, Build, Release
Reference Product Extension: product:* 객체·관계·지표
```

OCSF는 Security Profile의 이벤트 mapping 출발점이며 범용 Core 자체가 아니다. 다른 표준과 도메인은 별도 Profile·Adapter로 연결한다.

### 9.4 공통 메타데이터 계약

모든 Profile과 Extension은 최소한 다음 메타데이터를 유지한다.

```yaml
id: stable-id
type: profile-qualified-type
schema_version: 1.0.0
owner: accountable-owner
status: draft | approved | deprecated
provenance: source-reference
observed_at: optional-timestamp
effective_from: optional-timestamp
expires_at: optional-timestamp
confidence: optional-score
classification: data-classification
scope: tenant-workspace-project
```

### 9.5 신뢰·원장 경계

- Semantic mapping은 원본 값·출처·변환 버전과 lineage를 보존한다.
- Ontology의 관계는 가능한 구조를 정의하며 실제 관계 인스턴스는 권위 있는 원천에서 온다.
- Knowledge는 후보→리뷰→승인→폐기 수명주기를 가지며 유형별 책임자가 승인한다.
- Policy는 Knowledge 검색 결과가 아니라 승인 원장과 Policy Engine에서 집행한다.
- Evaluation의 기대 결과는 운영 컨텍스트와 격리해 평가 오염을 막는다.
- Domain Profile은 Core의 필수 메타데이터·Scope·출처 계약을 완화할 수 없다.
- Git은 스키마·정책 정의의 버전 원장일 수 있지만 운영 사실·고객 Evidence·비밀정보의 원장은 아니다.
- Obsidian은 연구·초안 도구일 수 있지만 공식 운영 원장은 아니다.
- 중앙 Registry·Knowledge Service는 여러 제품의 공동 배포 필요가 확인된 뒤 도입한다.

## 10. Persistent Context

규칙과 지식을 모델의 기억에만 의존하지 않는다.

```text
Project Artifacts + Foundation Version + Decisions
+ Context Receipt + Checkpoint + Evidence References
= 재구성 가능한 작업 컨텍스트
```

- **Context Receipt:** Unit, Foundation 버전, 적용 규칙, Knowledge·Semantic 참조, Agent 권한과 Scope
- **Checkpoint:** 완료·미완료 단계, 결정과 근거, 차단 요소와 다음 행동
- **복구:** 컨텍스트 압축·세션 종료·에이전트 교체 후 원본 산출물과 Checkpoint에서 재개

전체 Foundation과 Knowledge를 상시 프롬프트에 넣지 않는다. 오래된 대화보다 원본 Decision과 Evidence를 우선한다. Scope·승인·정책을 복구하지 못하면 고위험 실행은 중단한다.

## 11. Agent Integration

ISEKAI는 기존 에이전트를 실행 엔진으로 사용한다.

```text
ISEKAI
├─ Claude Adapter
├─ Codex Adapter
├─ Kiro Adapter
└─ Future Approved Agent Adapter
```

Adapter는 프로젝트·Unit 컨텍스트 전달, Foundation·Profile 버전 표시, Decision 대기 상태, Evidence·Checkpoint 연결과 capability 차이 보고를 담당한다.

Adapter는 모델의 추론 방식과 출력 스타일을 과도하게 교정하지 않는다. 상시 규율 주입, 도구 출력 압축·재작성과 코드 folding은 기본 기능이 아니다.

로컬 launcher 이름은 `isekai`이며 직접 CLI는 `isekai <action>`, Runtime Adapter 내부 호환 계약은 `isekai plugin <action>`을 사용한다.

### 11.1 Adapter 세션 모드

Runtime Adapter는 호스트에서 발견 가능한 상태를 유지하지만 ISEKAI workflow mode는 모든 새 대화에서 기본 `off`다. 이 모드는 Host plugin의 설치·enable 상태나 Unit lifecycle status와 별개다.

```text
새 대화: OFF
  ↓ /isekai on [--project PATH]
ACTIVE
  ↓ 컨텍스트 중단 또는 새 대화
OFF
  ↓ /isekai on
ACTIVE + status 또는 resume
  ↓ /isekai off
OFF
```

`on`은 현재 대화에서 선택한 Project의 ISEKAI mode만 활성화한다. Project·Foundation context와 Unit candidate 경로를 반환하지만 Unit을 선택·검증·resume하지 않으며 `unit`과 `active_unit`은 `null`이다. Unit 수와 관계없이 성공한다. 활성 중 새 요청은 `intake`를 거쳐 Query·Quick Change·Unit으로 라우팅한다.

기존 Unit 작업을 계속할 때는 `resume [--project PATH] [--unit PATH]`을 별도로 호출한다. `resume`만 Unit을 선택하고 Checkpoint와 원본 artifact를 복구한다. 여러 Unit이 있으면 명시적 `--unit`을 요구한다.

`off`는 자동 라우팅을 중단하지만 Unit, Decision, Evidence, Receipt와 Checkpoint를 변경하거나 삭제하지 않는다. 암묵적 checkpoint도 작성하지 않는다. 모드가 off인 상태의 명시적 `/isekai <action>`은 대화 모드를 활성화하지 않는 one-shot action이다.

Core는 `on`과 `off`를 읽기 전용 stateless handshake로 제공하며 mode를 artifact나 중앙 세션 저장소에 영속화하지 않는다. 실제 Host plugin enable/disable은 각 Agent CLI의 네이티브 기능을 사용한다.

Adapter는 `on`, `status`, `resume` 전에 Adapter version, Core version, protocol version과 Project lock을 `handshake`로 검증한다. 설치 파일 또는 Foundation digest가 lock과 다르거나 protocol이 호환되지 않으면 fail-closed하고 `doctor` 또는 명시적 update를 요구한다.

```json
{
  "adapter_mode": {
    "state": "on|off",
    "default_state": "off",
    "scope": "conversation",
    "persistent": false,
    "automatic_routing": true,
    "next_session_state": "off"
  }
}
```

`automatic_routing`은 `on`에서만 `true`다. `next_session_state`는 항상 `off`이며 새 세션은 이전 대화의 mode를 복구하지 않는다.

## 12. 실행 통제

초기 구현은 모든 로컬 도구를 프록시하지 않는다. 다음 고위험 부작용부터 외부 경계에서 통제한다.

- 보호 브랜치와 원격 Git 변경
- 프로덕션·클라우드·Kubernetes 변경
- 고객 데이터와 Engagement Scope
- 비밀정보·자격증명 사용
- 고위험 보안운영·진단·레드팀 도구

일반적인 로컬 탐색·작성·테스트에는 과도하게 개입하지 않는다. 강한 통제는 원격 IAM, 보호 브랜치, 승인 시스템과 격리 실행 환경에서 최종 집행한다.

## 13. 제품·서비스 적용

아래 보안 적용은 범용 AI-DLC Core를 검증하는 첫 사례다. 비보안 제품은 Software Delivery Profile 또는 별도 Domain Profile과 Product Extension을 선택하며 동일한 Workflow·Decision·Evidence 계약을 사용한다.

### 기준 제품

Foundation v0.1을 처음 검증할 실제 제품은 별도 Unit에서 선정한다. 선정된 제품의 기능과 Agent 협업을 AI-DLC로 개발하고, 검증된 공통 규칙·Semantic·Knowledge만 Foundation Core로 승격한다.

### 향후 제품

Foundation 버전과 Product Profile을 선택하고 제품별 Semantic·Knowledge·Evaluation Extension을 가진다. 공통 Core 의미는 변경하지 않는다.

### 취약점 진단·레드팀

Engagement, Scope, Finding, Evidence, Report와 Retest 계약을 사용한다. 고객별 데이터·자격증명·실행 환경을 격리하고 능동적 실행은 별도 인간 승인을 요구한다.

## 14. 단계적 구현

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

## 15. 첫 번째 백로그

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

## 16. MVP 성공 기준

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

## 17. 책임

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

## 18. 지표

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

## 19. 비목표

- 범용 OpenAgent·자체 Agent Brain·모델 라우터
- 모든 작업에 정식 AI-DLC 강제
- 상시 장문 규율 프롬프트와 출력 압축
- 모델 추론·출력 스타일 미세관리
- 모든 로컬 도구를 프록시하는 거대한 Gateway
- 초기부터 중앙 Registry·Portal·Control Plane 구축
- 제품 기능 없이 Foundation만 장기간 개발
- 거대한 온톨로지·Knowledge Graph 선행 구축
- 사람 승인 없는 자율 대응·진단·레드팀 실행

## 20. 남은 결정

1. Foundation 후속 버전의 소유자와 승인자
2. Unit Profile별 필수·선택 산출물
3. Foundation 저장소와 제품 저장소 경계
4. 첫 기준 제품 Unit
5. Shared Service 전환 기준
