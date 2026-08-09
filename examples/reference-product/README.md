# ISEKAI Reference Product

Reference Product는 작지만 실제적인 소프트웨어 변경을 통해 ISEKAI를
프로젝트 로컬 AI-DLC Runtime으로 검증한다. 도메인 Extension은
`FeatureProposal`을 Software Delivery Profile에 연결한다. `starter/`에는
의존성이 없는 시작 Python 제품이 있고, `completed/`에는 manifest,
Extension, 소스, 테스트, 전체 생명주기 기록을 담은 검증된 Golden Unit이
포함되어 있다.

## E2E 기능

호스트 에이전트 시뮬레이션은 결정적인 제안 우선순위 기능을 추가한다.

```text
high 영향도 → medium 영향도 → low 영향도 → 제안 ID로 동률 결정
```

시나리오는 설치된 프로젝트 로컬 launcher를 통해 다음 흐름을 증명한다.

```text
Codex Runtime Skill 설치
→ handshake와 Project init
→ on과 adaptive-unit intake
→ 승인된 Level-1 계획과 Execution Envelope
→ 권한이 확인된 소스·테스트 편집
→ 실패 인수 테스트 Evidence, 구현, 성공 테스트 Evidence
→ architecture/release/operation Decision
→ learned Unit, 유효한 verify, 정상 doctor
```

## Golden Unit

`completed/units/`는 pytest 임시 Project와 함께 생명주기 산출물이 사라지지
않도록 실제 결과를 보존한다. 체크인된 Unit에는 다음 항목이 포함된다.

- 의도, 요구사항, 아키텍처, 계획, 인수 조건, 릴리스, 운영, 구현 가이드
- Context Receipt와 승인된 adaptive Execution Envelope
- 권한 원장과 사람이 검토하는 Decision Packet 4개
- 변경 불가능한 실패·성공 Evidence와 현재 verification
- 최종 checkpoint와 `learned` Unit 상태

스냅샷은 실행 당시 ID, 시각, 무결성 digest를 보존한다. 머신 로컬 manifest와
명령 경로만 Project 상대 경로나 이식 가능한 값으로 정규화한 뒤 Unit의
무결성을 다시 결합하고 검증했다. E2E는 새 실행 결과를 Golden Unit의 산출물
구조, 문서 원문, 안정적인 생명주기 의미와 비교하며 새 ID, 시각, digest만
비교에서 제외한다. 또한 Project의 `document_language: ko`에 따라 사람이
검토하는 문서와 Decision 설명이 한국어인지 별도로 검증한다.

Golden Unit의 Inception, Architecture, Release, Operation Decision은 E2E가
`reference-product-owner`라는 사람 역할의 응답을 결정적으로 시뮬레이션한
기록이다. 실제 Runtime에서는 Agent가 선택지와 근거를 제안할 수 있지만,
사용자가 명시적으로 승인하거나 거부하기 전에는 `decision` action을 호출해
결과를 만들어내면 안 된다. Core는 Decision의 결박과 lifecycle Gate를
검증하고, 실제 사람 응답을 받았는지는 Runtime Skill이 보장한다.

완성 Project를 직접 테스트하고 Unit을 검증할 수 있다.

```bash
cd examples/reference-product/completed
PYTHONPATH=src ../../../.venv/bin/python -m unittest discover -s tests -v
../../../.venv/bin/python -m isekai unit-verify \
  units/unit-20260808-2fb9fd0ab8c9418fb4a6deebaa65e025
```

이 시나리오만 실행하려면 다음 명령을 사용한다.

```bash
uv run pytest tests/test_reference_product_e2e.py -q
```

이 테스트는 원격 모델이나 실제 Codex UI 세션을 실행하지 않는다. Runtime
Skill이 사용하는 것과 같은 설치된 launcher와 Runtime action 계약을 통해
호스트 에이전트를 결정적으로 시뮬레이션한다. 모델 없는 설치 스모크는 다음
명령으로 별도 실행한다.

```bash
uv run python scripts/live-smoke.py
```

인증과 비용이 필요한 Codex 스모크는 명시적으로 선택해 실행한다.

```bash
uv run python scripts/live-smoke.py --runtime codex --host codex
```

실제 호스트 인수 경계와 기록된 Runtime 결과는 `docs/live-smoke.md`에서
확인한다.
