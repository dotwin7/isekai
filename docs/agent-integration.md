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

로컬 launcher 이름은 `isekai`이며 직접 CLI는 `isekai <action>`, Runtime Adapter 내부 호환 계약은 `isekai plugin <action>`을 사용한다.

## Adapter 세션 모드

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

Adapter는 `on`, `status`, `resume` 전에 Adapter version, Core version, protocol version과 Project lock을 `handshake`로 검증한다. Project lock이 없거나 설치 파일 또는 Foundation digest가 lock과 다르거나 protocol이 호환되지 않으면 fail-closed하고 설치, `doctor` 또는 명시적 update를 요구한다.

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
