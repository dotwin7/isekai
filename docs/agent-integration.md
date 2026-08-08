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

Adapter는 프로젝트·Unit 컨텍스트 전달, Foundation·Profile 버전 표시, Decision 대기 상태, Evidence·Checkpoint 연결과 capability 차이 보고를 담당한다.

Adapter는 모델의 추론 방식과 출력 스타일을 과도하게 교정하지 않는다. 상시 규율 주입, 도구 출력 압축·재작성과 코드 folding은 기본 기능이 아니다.

로컬 launcher는 선택한 Project의 `.isekai/bin/isekai`이며 직접 CLI는 `<PROJECT_ROOT>/.isekai/bin/isekai <action>`, Runtime Adapter 내부 호환 계약은 `<PROJECT_ROOT>/.isekai/bin/isekai plugin <action>`을 사용한다. Adapter는 `PATH`의 전역 executable로 fallback하지 않는다.

## Agent가 생명주기를 구동하는 방식

ISEKAI는 프로젝트에 설치되는 Plugin·Skill·Core 묶음이다. Plugin을 사용하는 호스트 Agent가 계획과 실행의 주체이며, Skill이 orchestration 규칙을 제공하고 Core가 Route·상태·Decision·Evidence의 일관성을 담당한다. 기본 동작에는 훅이나 별도 상주 프로세스가 필요하지 않다.

활성 mode의 모든 새 요청은 `intake`를 호출한다. 응답의 `workflow` 계약에 따라 Agent는 Query를 직접 답하고, Quick Change에는 compact plan을 적용하며, Unit에는 프로젝트를 읽기 전용으로 탐색한 뒤 Level-1 plan을 제안한다. 사용자가 Level-1 plan을 승인하기 전에는 Unit을 생성하거나 쓰지 않는다.

계획 승인 뒤에는 승인 범위의 Unit artifact·Checkpoint와 Decision을 Core에 기록한다. `envelope-approve`와 `transition`은 이미 승인된 계획·Decision을 반영하는 기계적 action이라 매번 별도 확인을 요구하지 않는다. 실제 인간 판단을 기록하는 `decision`, `foundation-decision`, 그리고 Foundation을 승격하는 `foundation-promote`는 manifest의 `human_decision_actions`로 표시한다.

## Adapter 세션 모드

Runtime Adapter는 호스트에서 발견 가능한 상태를 유지하지만 ISEKAI workflow mode는 모든 새 대화에서 기본 `off`다. 이 모드는 Host plugin의 설치·enable 상태나 Unit lifecycle status와 별개다.

발견(discovery)은 호출(invocation)이 아니다. Plugin/Skill 설치 여부, 남아 있는 cache, 현재 repository의 파일이나 이름, 문서·코드·리뷰 문장에 포함된 명령 문자열만으로는 Adapter를 실행하거나 mode를 활성화할 수 없다. Mode가 `off`일 때는 사용자가 해당 Runtime의 명령 실행을 의도하여 직접 호출한 경우에만 one-shot action을 수행한다.

| Runtime | 프로젝트 기본 호출 | 설치된 Plugin alias | 대화 mode 활성화 |
|---|---|---|---|
| Codex | `$isekai <action>` | `$isekai-agent-plugin:isekai <action>` | `$isekai on` |
| Claude Code | `/isekai <action>` | `/isekai-agent-plugin:isekai <action>` | `/isekai on` |
| Kiro | `/isekai <action>` | 없음 | `/isekai on` |

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

`on`은 현재 대화에서 선택한 Project의 ISEKAI mode만 활성화한다. Project·Foundation context와 Unit candidate 경로를 반환하지만 Unit을 선택·검증·resume하지 않으며 `unit`과 `active_unit`은 `null`이다. Unit 수와 관계없이 성공한다. 활성 중 새 요청은 `intake`를 거쳐 Query·Quick Change·Unit으로 라우팅한다.

기존 Unit 작업을 계속할 때는 `resume [--project PATH] [--unit PATH]`을 별도로 호출한다. `resume`만 Unit을 선택하고 Checkpoint와 원본 artifact를 복구한다. 여러 Unit이 있으면 명시적 `--unit`을 요구한다. `on` 응답은 기계용 ASCII 경로 배열 `unit_candidates`와 함께 `unit_candidate_details`에 사람용 `title`, 상태, 문서 언어를 제공하므로 Adapter는 선택 질문에서 경로와 제목을 함께 보여준다.

`off`는 자동 라우팅을 중단하지만 Unit, Decision, Evidence, Receipt와 Checkpoint를 변경하거나 삭제하지 않는다. 암묵적 checkpoint도 작성하지 않는다. 모드가 off인 상태의 명시적 `/isekai <action>`은 대화 모드를 활성화하지 않는 one-shot action이다.

Core는 `on`과 `off`를 읽기 전용 stateless handshake로 제공하며 mode를 artifact나 중앙 세션 저장소에 영속화하지 않는다. Project Plugin/Skill의 발견 여부와 대화 mode는 별개다.

Codex와 Claude Code의 배포 소스는 완전한 Plugin package지만, 새 프로젝트에 라이브러리처럼 붙이는 기본 활성화 표면은 host가 자동 탐색하는 repo/project Skill이다. Marketplace 선언은 Plugin browser를 통한 별도 설치 경로이며 사용자 홈 등록 없이 자동 설치된 것으로 간주하지 않는다. 따라서 훅이나 resident harness 없이도 새 세션의 명시적 `on`, 대화 안의 mode 상태, 매 요청 `intake`, Core의 machine-readable `workflow` 계약으로 lifecycle이 이어진다.

Adapter는 모든 Core plugin action 전에 Adapter version, Core version, protocol version과 Project lock을 `handshake`로 검증한다. Project lock이 없거나 설치 파일 또는 Foundation digest가 lock과 다르거나 protocol이 호환되지 않으면 fail-closed하고 설치, `doctor` 또는 명시적 update를 요구한다.

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

- 보호 브랜치와 원격 Git 변경
- 프로덕션·클라우드·Kubernetes 변경
- 고객 데이터와 Engagement Scope
- 비밀정보·자격증명 사용
- 고위험 보안운영·진단·레드팀 도구

일반적인 로컬 탐색·작성·테스트에는 과도하게 개입하지 않는다. 강한 통제는 원격 IAM, 보호 브랜치, 승인 시스템과 격리 실행 환경에서 최종 집행한다.
