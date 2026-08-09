# Project Knowledge

- 상태: Canonical
- 작성일: 2026-08-09
- 설명: Unit에서 발견한 재사용 지식을 후속 Unit에 전달하는 프로젝트 범위 계약

## 목적과 경계

Project Knowledge는 한 Unit에서 확인한 용어, 관례, 지침, 판단을 이후 Unit에서도 일관되게 사용하기 위한 작은 공유 계층이다. 도메인 온톨로지나 의미 추론 엔진이 아니며 Foundation·Profile·Extension을 대신하지 않는다.

| 계층 | 책임 |
|---|---|
| Foundation·Profile·Extension | 규범, 도메인 계약, 실행에 적용되는 규칙 |
| Project Knowledge | 검토된 프로젝트 공통 설명과 관례 |
| Unit artifact | 해당 작업의 Decision, Evidence, Checkpoint와 작업 결과 |

Project Knowledge 항목은 실행 권한을 넓히거나 정책을 약화할 수 없다. Core authorization은 이 지식을 의미적으로 해석하지 않으며 계속 경로, Envelope, agent level과 승인 원장만 결정론적으로 검사한다.

## 승격 흐름

```text
Unit의 근거 artifact
  → 불변 Project Knowledge candidate
    → 사람의 knowledge Decision
      → 승인된 Project Knowledge release
        → 이후 생성되는 Unit의 Context Receipt에 고정
```

1. `operating` 또는 `learned` Unit에서 재사용할 항목을 제안한다.
2. Core는 각 항목의 Unit-relative 근거 파일과 SHA-256 digest를 candidate에 결박한다.
3. Adapter는 candidate 전체, 적용 범위, 대안, tradeoff와 위험을 사람에게 보여 준다.
4. 사람의 `decision --gate knowledge`가 candidate ID·digest·reference를 정확히 결박한다.
5. `project-knowledge-promote`가 승인 결정을 재검증하고 다음 patch release를 원자적으로 추가한다.

`project-knowledge-promote` 자체는 이미 기록된 사람 결정을 적용하는 기계적 action이다. 실제 사람 확인은 manifest의 `human_decision_actions`에 포함된 `decision`을 호출하기 전에 받는다. 승인 뒤 candidate나 근거 artifact가 바뀌었거나 다른 release가 먼저 승격되어 candidate의 base가 낡았으면 승격은 실패한다.

## 저장 구조

```text
project-knowledge/
├── catalog.json
└── candidates/
    └── PKC-....json
```

- Candidate는 덮어쓰지 않는 제안 기록이다.
- `catalog.json`은 release 전체 이력과 digest chain을 가진 단일 원자적 원장이다.
- 최초 release는 `0.1.0`, 이후 승격은 patch version을 증가시킨다.
- 기존 항목은 같은 ID로 수정하지 않는다. 새 ID와 `replaces`를 사용하면 이전 항목은 `deprecated`로 남고 새 항목이 `approved`가 된다.
- 항목 kind는 `term`, `convention`, `guidance`, `decision`만 사용한다.

Git 저장소 안의 이 파일들이 프로젝트 원장이다. 중앙 Knowledge Service나 별도 데이터베이스는 필요하지 않다. 비밀, 고객 원본 데이터, 대용량 Evidence는 Project Knowledge에 넣지 않는다.

## Unit별 버전 고정

새 Unit은 생성 순간의 최신 승인 release ID·version·digest와, 그 release에서 Unit `work_scope`와 겹치는 활성 항목만 `context-receipt.json`의 `project_knowledge`에 결박한다. Scope 비교는 의미 추론이 아니라 wildcard 이전의 literal path prefix가 겹치는지 보수적으로 판단한다. 불확실한 wildcard는 포함하는 쪽으로 처리하며 Unit scope가 비어 있으면 활성 항목 전체를 고정한다. `deprecated` 항목은 새 Unit Context에서 제외한다.

Receipt의 `selection`은 선택 mode, Unit work scope, 전체 활성 항목 수와 선택된 항목 수를 기록하고 `context_digest`가 결과를 결박한다. Project Knowledge가 갱신되어도 진행 중이거나 완료된 Unit의 Receipt는 바뀌지 않으며 새 Unit만 최신 release에서 다시 선택한다.

선택된 Unit의 `status`와 `resume`은 현재 catalog가 아니라 해당 Receipt의 scope-selected 고정본을 반환한다. Unit을 선택하지 않은 `on`·Project status·`resolve`는 release ID·version·digest와 활성·폐기 항목 수만 반환해 전체 지식을 프롬프트에 주입하지 않는다. Unit Envelope로 `project-knowledge/`를 직접 읽거나 수정하거나 테스트하는 authorization은 거부한다. Project 범위에서 전체 최신 상태를 명시적으로 보려면 Core의 `project-knowledge-status`를 사용한다. 이 응답은 candidate별 ID, 출처 Unit, 항목 ID, digest, `unpromoted|promoted|invalid` 상태, 승격 release와 검증 문제를 함께 보여 준다. 이 경계 덕분에 형제 Unit을 직접 읽지 않고도 승인된 공통 지식만 후속 Unit에 전달된다.

## CLI 예시

```bash
./.isekai/bin/isekai project-knowledge-propose \
  --unit units/unit-... \
  --entries-json '[{"id":"service-id-format-v1","kind":"convention","title":"서비스 ID","statement":"소문자 kebab-case를 사용한다.","scope":["services/**"],"owner":"platform-team","references":["architecture.md"]}]' \
  --proposed-by learning-agent

./.isekai/bin/isekai decision \
  --unit units/unit-... \
  --gate knowledge \
  --outcome approved \
  --summary "프로젝트 공통 규칙으로 승인" \
  --rationale "후속 Unit에도 동일하게 적용해야 한다." \
  --reference project-knowledge/candidates/PKC-....json \
  --decided-by human-owner

./.isekai/bin/isekai project-knowledge-promote \
  --unit units/unit-... \
  --candidate project-knowledge/candidates/PKC-....json

./.isekai/bin/isekai project-knowledge-status --project .
```
