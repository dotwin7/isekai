# Agent Integration

- 설명: Runtime Adapter 구조, Adapter 세션 모드와 실행 통제
- 문서 역할: [ISEKAI canonical 문서 집합](isekai.md)의 일부

## Adapter 구조

ISEKAI는 기존 에이전트를 실행 엔진으로 사용한다.

```text
ISEKAI
├─ Claude Adapter
├─ Codex Adapter
├─ Kiro Adapter
└─ Future Approved Agent Adapter
```

Adapter는 프로젝트·Unit 컨텍스트 전달, Foundation·Profile 버전 표시, Decision 대기 상태, Evidence·Checkpoint 연결과 ISEKAI Catalog entry 상태 보고를 담당한다. `catalog-status`와 MCP resource로 설치된 Catalog entry를 읽되, catalog나 `preview` 상태를 새로운 실행 권한으로 해석하지 않는다.

Adapter는 모델의 추론 방식과 출력 스타일을 과도하게 교정하지 않는다. 상시 규율 주입, 도구 출력 압축·재작성과 코드 folding은 기본 기능이 아니다.

로컬 launcher는 선택한 Project의 `.isekai/bin/isekai`이며 직접 CLI는 `<PROJECT_ROOT>/.isekai/bin/isekai <action>`, Runtime Adapter 내부 호환 계약은 `<PROJECT_ROOT>/.isekai/bin/isekai runtime <action>`을 사용한다. Adapter는 `PATH`의 전역 executable로 fallback하지 않는다.

## Agent가 생명주기를 구동하는 방식

ISEKAI는 프로젝트에 설치되는 Runtime Skill·Core·Foundation 묶음이다. 호스트 Agent가 계획과 변경 내용을 제안하고, Skill이 orchestration 규칙을 제공하며 Core가 Route·상태·Decision·Evidence와 실제 쓰기 경계를 담당한다. lifecycle 훅은 이 경계의 구성요소가 아니다.

활성 mode에 끝나지 않은 active Unit이 없을 때만 새 요청에 `intake`를 호출한다. 응답의 `workflow` 계약에 따라 Agent는 Query를 직접 답하고, Quick Change에는 compact plan을 적용하며, Unit에는 프로젝트를 읽기 전용으로 탐색한 뒤 Project의 `maximum_agent_level`을 넘지 않는 계획을 제안한다. 사용자가 전체 계획을 승인하기 전에는 Unit을 생성하거나 쓰지 않는다.

계획 승인 뒤에는 승인 범위의 Unit artifact·Checkpoint와 Decision을 Core에 기록한다. `envelope-approve`와 `transition`은 이미 승인된 계획·Decision을 반영하는 기계적 action이라 매번 별도 확인을 요구하지 않는다. 실제 인간 판단을 기록하는 `amend`, `active-unit-detach`, `decision`, `foundation-decision`, 그리고 Foundation을 승격하는 `foundation-promote`는 manifest의 `human_decision_actions`로 표시한다.

계획을 대화에 제시한 것과 Unit에 기록한 것은 별개다. `unit-init` 직후 Agent는 승인된 계획을 `plan.md`에 그대로 영속화하고 Inception 문서와 stage disposition, Checkpoint를 materialize한 뒤에만 첫 transition을 수행한다. Construction 구현이 끝나면 Architecture와 Implementation Guide를 기록하고 Architecture packet을 승인받은 뒤 Validation으로 간다. Core의 artifact readiness와 Decision snapshot digest가 이 순서를 fail-closed로 검사한다.

중간 구현 기록도 종료 시점의 선택 사항이 아니다. `managed-edit`·`managed-test`·`external-api`는 후속 Checkpoint 의무를 반환하며, Agent는 작업 batch 직후 `artifact-write`로 관련 stage 문서를 갱신하고 `completed`·`pending`·`blocked_by`·`next_action`을 기록한다. 진행 보고, Human Gate, Unit 전환, 최종 응답이나 의도적 중단 전에도 같은 기록이 필요하다. 예기치 않은 이탈 뒤 `resume`이 stale checkpoint를 보고하면 새 구현을 시작하지 않고 authorization target과 workspace를 대조해 복구 Checkpoint부터 기록한다.

`unit-init`하거나 `resume`한 Unit은 최종 Operation Decision을 승인받아 `learned`가 될 때까지 Project의 active work boundary다. Core는 이 결박을 ignored `.isekai-runtime/active-unit.json`에 저장한다. 그 전에 사용자가 추가·삭제·행동 변경을 말하면 새 `intake`나 Quick Change로 빼지 않고 먼저 `amend`로 정확한 요청, 요청자, 영향받는 Unit 문서를 기록한다. 명시적으로 다른 작업 시작·현재 Unit 포기·다른 Unit 전환을 요청한 경우에만 현재 Checkpoint를 보존하고 사람 판단 action인 `active-unit-detach`를 기록한 뒤 경계를 바꾼다.

`amend`는 사용자의 변경 승인을 append-only `amendments.json`과 Amendment Decision에 함께 결박한다. Core는 영향 문서에 따라 같은 Unit을 Inception·Construction·Validation·Operating 중 필요한 지점으로 되돌리고 기존 Evidence를 무효화한다. Agent는 해당 문서와 구현을 갱신하고 검증·Checkpoint를 기록한 뒤 amendment ID를 참조하는 새 lifecycle Decision을 받아야 한다. 사용자가 제시된 gate 결과 자체를 거부한 경우만 `rejected`로 기록하며, 일반 변경 요청을 거부로 바꾸지 않는다.

MCP Core는 Host 대화 원문을 자동 수신하지 않는다. active Unit의 새 사용자 메시지를 `amend`로 보고하는 것은 Runtime Adapter의 attested routing 책임이다. 보고된 뒤의 동일 Unit 유지·되감기·문서 변경·Decision·Checkpoint는 Core가 강제하고, 새 `intake`를 시도해도 `active-unit-amendment-required`로 차단한다. 그러나 Adapter가 메시지를 아예 누락했다는 사실은 lifecycle 훅이나 Host 플러그인으로 대화를 가로채지 않는 현재 구조에서 Core가 독립적으로 탐지할 수 없다. Manifest는 이를 `conversation_change_reporting: runtime-adapter-attested-not-core-observed`로 공개한다.

Manifest의 `trust_model`은 Core가 레코드 일관성·변경 탐지·active Unit 결박과 managed edit/test 실행을 강제하지만 사람 신원 확인과 외부 API 실행은 Runtime·CI·외부 승인 시스템의 경계라는 점을 기계적으로 공개한다. 새 Decision과 Evidence의 attestation도 같은 경계를 digest에 결박한다. Adapter는 `human_decision_actions` 전에 실제 호스트 사용자 확인을 받아야 하며 caller가 적은 actor 문자열을 인증 결과처럼 표현해서는 안 된다.

## Core 전용 실행 경계

ISEKAI 호스트 프로필은 Project 파일시스템을 호스트 Agent에게 읽기 전용으로 노출하고 Project-local `isekai-core` MCP server만 변경 통로로 연결한다. Codex의 일반 write/apply-patch, Claude의 Edit·Write·NotebookEdit·Bash, Kiro의 write·shell 도구는 이 프로필에서 사용할 수 없다. `handshake`는 Project 설정의 read-only 선언, 필수 MCP 연결과 Core tool allowlist를 확인하지 못하면 lifecycle action 전에 실패한다.

이 검사는 Project-local 설정의 정합성 검사다. 실제 Host process가 더 높은 우선순위의 CLI flag나 조직 managed policy로 Project 설정을 덮어썼는지 Core가 stdio MCP 안에서 독립적으로 증명할 수는 없다. 따라서 정상 ISEKAI 실행 보호 설정에서는 사전에 direct writer가 제공되지 않으며, 사용자가 별도의 Host override로 sandbox를 다시 연 것은 운영자 우회로 취급한다. lifecycle 훅을 원하지 않는 조건에서 이 Host permission이 MCP 밖 직접 파일 수정을 사전 차단하는 경계이고, Core는 자기 MCP를 거친 요청을 다시 active Unit·Envelope·Decision으로 검사한다.

## ISEKAI Catalog 연결

ISEKAI가 제공하는 기능은 Project-local Core MCP의 공통 Catalog에 등록된다. Core MCP는 Catalog와 개별 Catalog entry manifest resource를 노출하고 Unit Context Receipt는 Catalog digest를 고정한다. Adapter는 `active` entry만 사용하며, 각 entry action은 같은 active Unit·Envelope·Decision·Evidence 경계를 거쳐야 한다. 현재 등록된 실행 기능은 AI-DLC이며, 새 기능은 구현과 배포 계약이 준비된 뒤 고유한 entry ID로 추가한다. 구체 계약은 [ISEKAI Catalog](catalog.md)를 따른다.

파일 변경은 grant와 실행을 분리하지 않는다. `artifact-write`는 승인 전 Unit 문서를 materialize하고, 승인된 문서의 의미 변경에는 먼저 같은 Unit의 pending Amendment를 요구한다. 기존 acceptance 항목을 `[ ]`에서 `[x]`로만 진행시키는 것은 예외지만 문구 변경이나 역방향 변경은 허용하지 않는다. `managed-edit`는 모든 target의 Envelope 범위와 expected digest를 검증하고 한 Core batch에서 쓰기와 receipt 기록을 완료한다. `managed-test`는 descriptor 검증으로 만든 일회용 Project 복제본을 macOS Seatbelt 또는 Linux Bubblewrap 안에서 실행한다. 원본 Project·사용자 홈 file data read, workspace 밖 write와 network를 OS 정책으로 차단하고 process/resource/output 상한을 적용하며 provider가 없으면 실행하지 않는다. Runtime `authorize --action edit|test`는 의도적으로 거부된다. 이 구조는 훅의 사후 감지에 의존하지 않는다.

이 프로필 밖에서 사용자가 IDE나 별도 프로세스로 직접 파일을 바꾸는 행위까지 같은 OS 사용자 권한으로 금지하는 것은 Core의 범위가 아니다. 그런 외부 변경은 다음 Core write의 expected digest나 artifact snapshot 검증에서 충돌로 처리한다. 호스트 실행 옵션이나 조직 관리 정책이 Project 설정을 덮어쓸 수 있는 환경은 해당 상위 정책에서도 read-only/MCP 전용 경계를 강제해야 한다.

사람 확인은 도구 호출 때마다 받는 것이 아니라 판단의 대상이 완성되는 lifecycle 경계에서 받는다.

| 시점 | 사람이 확인하는 대상 | 다음 단계 |
|---|---|---|
| 자율성 제한 plan 제안 뒤 | 전체 Intent·Scope·단계·위험·Execution Envelope | `unit-init`과 Inception 기록 |
| `awaiting-inception-decision` | 최종 Inception packet과 정확한 Envelope ID·digest | Construction |
| Construction 설계 완료 뒤 | Architecture Decision packet | Validation |
| passing Evidence 생성 뒤 | Evidence ID·digest, 잔여 위험, rollback | Releasing |
| 운영 결과 검토 뒤 | Operation Decision packet | Learned |
| Foundation 변경 승격 전 | Foundation Decision과 passing release Evidence | Foundation promotion |

계획 응답에 최종 Inception packet과 정확한 Envelope까지 모두 포함되어 있고 사용자가 그 전체를 명시적으로 승인했다면 같은 응답을 Inception Decision의 근거로 기록할 수 있다. 범위·단계·위험·외부 효과 또는 Envelope가 이후 달라지면 기존 승인을 재사용하지 않고 다시 확인한다. `status`와 `resume`은 다음 transition, 필요한 gate, 승인 상태와 차단 여부를 `human_gate`로 반환하므로 Adapter는 이 값을 보고 Decision packet을 제시하거나 계속 진행한다.

Runtime의 파일·shell 도구 권한은 특정 도구 실행을 허가할 뿐 lifecycle Decision이 아니다. `dontAsk`, bypass permission, trust-all, unattended/headless 실행은 사람의 Decision을 새로 만들 수 없다. Core도 사람의 신원을 인증하지 않으므로 실제 강제가 필요한 배포 환경은 인증된 호스트 확인 UI나 외부 승인 시스템이 Decision 기록 주체를 통제해야 한다.

## Adapter 세션 모드

Runtime Adapter는 호스트에서 발견 가능한 상태를 유지하지만 ISEKAI workflow mode는 모든 새 대화에서 기본 `off`다. 이 모드는 Runtime Skill의 설치·발견 상태나 Unit lifecycle status와 별개다.

발견(discovery)은 호출(invocation)이 아니다. Skill 설치 여부, 남아 있는 cache, 현재 repository의 파일이나 이름, 문서·코드·리뷰 문장에 포함된 명령 문자열만으로는 Adapter를 실행하거나 mode를 활성화할 수 없다. Mode가 `off`일 때는 사용자가 해당 Runtime의 명령 실행을 의도하여 직접 호출한 경우에만 one-shot action을 수행한다.

| Runtime | 프로젝트 호출 | 대화 mode 활성화 |
|---|---|---|
| Codex | `$isekai <action>` | `$isekai on` |
| Claude Code | `/isekai <action>` | `/isekai on` |
| Kiro | `/isekai <action>` | `/isekai on` |

단순히 위 명령을 질문·인용·설명하는 문장은 호출이 아니다. 명시적 `on` 이전에는 Adapter가 Project/Foundation/Unit context를 읽거나 launcher, handshake, Core, `intake`, `route`, `inception`, `status`, `resume`을 자동 실행해서는 안 된다. `on` 이외의 명시적 action은 one-shot이며 mode를 켜지 않는다.

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

`on`은 현재 대화에서 선택한 Project의 ISEKAI mode만 활성화한다. Project·Foundation context와 Unit candidate 경로를 반환하지만 Unit을 새로 선택·검증·resume하지 않으며 `unit`은 `null`이다. 대신 Core가 이미 결박한 unfinished Unit이 있으면 `active_unit_binding`으로 알려 준다. Unit 수와 관계없이 성공한다. 결박이 없는 동안의 새 요청만 `intake`를 거쳐 Query·Quick Change·Unit으로 라우팅한다.

기존 Unit 작업을 계속할 때는 `resume [--project PATH] [--unit PATH]`을 별도로 호출한다. `resume`만 Unit을 선택하고 Checkpoint와 원본 artifact를 복구한다. 여러 Unit이 있으면 명시적 `--unit`을 요구한다. `on` 응답은 기계용 ASCII 경로 배열 `unit_candidates`와 함께 `unit_candidate_details`에 사람용 `title`, 상태, 문서 언어를 제공하므로 Adapter는 선택 질문에서 경로와 제목을 함께 보여준다.

Core가 결박한 Unit 하나만 Project의 persistent work에 대한 active Unit이며, `learned`(Operation 승인 뒤) 또는 `abandoned`(Abandonment 승인 뒤) 전까지 새 대화에서도 이 경계가 유지된다. Core는 active Unit이 있을 때 새 `intake`·`route`·`inception`·`unit-init`과 형제 Unit을 대상으로 한 persistent action을 fail-closed로 거부한다. 다른 Unit을 계속하려면 현재 Checkpoint를 먼저 보존하고 전환 결과를 사용자에게 설명한 뒤, 명시적 사용자 판단으로 `active-unit-detach`를 기록하고 해당 경로를 지정한 `resume --unit PATH`를 사용한다. 프로젝트 소스와 고정 Foundation·Profile·Extension은 이 격리 대상이 아니며 계속 Envelope와 Context Receipt의 경계를 따른다. 재사용할 Unit 학습은 `project-knowledge-propose`로 후보화하고 실제 사람의 Knowledge Decision 뒤 `project-knowledge-promote`로 승격한다. Adapter는 active Unit에서 `project-knowledge/`를 직접 읽지 않고 `context.project_knowledge`에 고정된 release만 사용한다.

`off`는 자동 라우팅을 중단하지만 Unit, Decision, Evidence, Receipt와 Checkpoint를 변경하거나 삭제하지 않고 Core active Unit 결박도 해제하지 않는다. 암묵적 checkpoint도 작성하지 않는다. 모드가 off인 상태의 명시적 `/isekai <action>`은 대화 모드를 활성화하지 않는 one-shot action이지만 active Unit 경계는 그대로 적용된다.

Core는 `on`과 `off`를 읽기 전용 handshake로 제공하며 대화 mode 자체는 artifact나 중앙 세션 저장소에 영속화하지 않는다. 다만 mode와 별개인 Project active Unit 결박은 `.isekai-runtime/active-unit.json`에 영속화해 새 대화와 one-shot action에도 같은 경계를 적용한다. Project Skill의 발견 여부, 대화 mode, active Unit 결박은 서로 다른 상태다.

새 프로젝트에 라이브러리처럼 붙이는 활성화 표면은 host가 자동 탐색하는 repo/project/workspace Skill이다. 따라서 훅이나 resident harness 없이도 새 세션의 명시적 `on`, 대화 안의 mode 상태, Core가 Project에 저장한 active Unit 결박, 결박이 없을 때의 `intake`, active Unit의 `amend`로 lifecycle이 이어진다.

Adapter는 모든 Core runtime action 전에 Adapter version, Core version, protocol version과 Project lock을 `handshake`로 검증한다. Project lock이 없거나 설치 파일 또는 Foundation digest가 lock과 다르거나 protocol이 호환되지 않으면 fail-closed하고 설치, `doctor` 또는 명시적 update를 요구한다.

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

## 실행 통제

초기 구현은 모든 로컬 도구를 프록시하지 않는다. 다음 고위험 부작용부터 외부 경계에서 통제한다.

Project의 `maximum_agent_level`은 action 상한이다. `L0`은 승인된 Envelope에서도 `read`만 허용하고, `L1`은 승인된 scope·stage·iteration 예산 안에서 `read`, `edit`, `test`를 허용한다. `L2`는 여기에 정확히 allowlist된 개발·테스트 환경의 `external-api`를 추가한다. Adapter는 Context Receipt의 값을 읽고 상한을 넘는 Envelope를 제안하지 않아야 하며, Core도 제안·승인·authorize·verify에서 같은 상한을 fail-closed로 집행한다. `L2`도 `credential-access`를 허용하지 않으며 아래 고위험 action은 계속 금지된다.

- 보호 브랜치와 원격 Git 변경
- 프로덕션·클라우드·Kubernetes 변경
- 고객 데이터와 Engagement Scope
- 비밀정보 원문 읽기·기록·전달
- 고위험 보안운영·진단·레드팀 도구

일반적인 로컬 탐색·작성·테스트는 선택한 agent level과 승인된 Envelope 안에서 수행한다. 강한 통제는 원격 IAM, 보호 브랜치, 승인 시스템과 격리 실행 환경에서 최종 집행한다.

### L2 외부 API 계약

L2는 API key를 Agent에게 전달하는 level이 아니다. 사용자는 key를 호스트의 secret store 또는 보호된 환경에 모델 컨텍스트 밖에서 설정하고, Envelope에는 `secret://provider/name` 형식의 불투명한 `credential_ref`만 기록한다. Adapter와 Core는 실제 값을 조회·표시·저장하지 않는다.

`external_access` 정책은 `development` 또는 `test` 환경, 소문자 외부 DNS host, HTTPS, query가 없는 path pattern, HTTP method, credential reference, `max_requests`를 모두 명시한다. `external-api` action과 정책 전체는 Envelope `approval_digest`에 포함되므로 목적지·method·reference·환경·예산 중 하나라도 바뀌면 새 Inception Decision이 필요하다. 각 호출 직전 `authorize --action external-api --target HTTPS_URL --method METHOD --credential-ref secret://provider/name`으로 grant를 받고, 호스트가 그 grant의 정확한 요청에만 비밀을 주입한다. Core는 원장을 검증하지만 실제 네트워크 실행·secret resolution·redaction은 호스트 경계이므로 Runtime sandbox와 출력 마스킹이 함께 필요하다.

프로덕션 endpoint, 고객 데이터, 배포, 원격 Git, cloud/Kubernetes 변경, 브라우저 세션 자격증명, HTTP, query-string credential, 무제한 목적지는 L2 범위가 아니다. 외부 통합 테스트 Evidence의 command에는 일반 `test` grant와 함께 선행 `external-api` grant ID를 `external_authorization_ids`로 연결하며, raw response는 Core artifact에 저장하지 않는다.
