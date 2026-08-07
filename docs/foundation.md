# Engineering Foundation과 Security Profile

- 설명: Foundation 계층 구조, 규칙 계약, v0.1 완료 조건과 승인 절차
- 문서 역할: [ISEKAI canonical 문서 집합](isekai.md)의 일부

## Foundation 계층

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

## 문서 언어 정책

문서 언어 정책은 Project의 `document_language`로 정한다. 기본값은 `ko`이며 `intent.md`, `requirements.md`, `architecture.md`, `implementation-guide.md`, `plan.md`, `acceptance.md`, `release.md`, `operations.md`와 Decision 설명은 한국어로 생성한다. `id`, JSON key, enum, CLI 명령, 코드와 로그는 호환성을 위해 영어를 유지한다. Project가 `document_language: "en"`을 지정하면 Human-facing template만 영어로 생성한다.

## 규칙 계층

규칙 계층은 다음과 같다.

```text
법·회사 보안정책
→ Foundation MUST 규칙
→ 제품·서비스 Profile
→ 저장소·컴포넌트 Extension
→ Unit별 승인된 Exception
```

하위 규칙은 상위 MUST 규칙을 완화할 수 없다. 예외에는 이유, 책임자, 보완 통제와 만료일이 필요하다. MUST 규칙은 자연어 `title`만으로 정의하지 않고 `condition.type`과 판정 필드를 함께 가져야 하며, Core가 이해하지 못하는 condition은 fail-closed로 거부한다. 적용 Context에는 rule ID가 아니라 적용 rule 전문을 포함한다.

## Foundation v0.1 완료 조건

- 규칙별 ID, MUST·SHOULD·MAY, 소유자와 적용 범위
- 개발·운영·Agent·Knowledge·Semantic 규칙
- Product Profile, Extension과 Exception 계약
- 인간 결정 게이트와 책임자
- 공통 Evaluation·Release 기준
- 자동 검사와 수동 리뷰 항목 구분
- 서로 다른 제품과 서비스에 적용 가능한 제품 중립성

Foundation v0.1은 첫 제품 Unit보다 먼저 확정한다. 실제 적용에서 발견한 빈틈은 후속 버전으로 개정한다.

## Foundation v0.1 승인 절차

Foundation 승인은 다음 두 영속 산출물을 모두 요구한다.

```text
foundation/
├─ decisions.json
└─ evidence/
   └─ release.json
```

`decisions.json`의 최신 Foundation release Decision이 `approved`여야 하며, `evidence/release.json`에는 모든 release check가 passing이어야 한다. 각 check의 provenance 시각은 Evidence 기록 시각보다 늦을 수 없다. Decision과 Evidence는 promotion이 관리하는 release·asset·Knowledge entry의 status를 제외한 등록 JSON과 catalog가 참조하는 Knowledge 본문 전체의 `approval_digest`를 함께 기록한다. 승인이나 검증 후 Foundation 내용이 달라지면 promotion은 실패하며 새 Decision과 Evidence가 필요하다. 두 조건이 모두 충족될 때만 `foundation-promote`가 `release.json`, 모든 등록 asset과 그 안의 Knowledge entry 상태를 `approved`로 승격한다. 승인 Decision이나 passing Evidence가 없으면 명령은 실패하고 Foundation 파일을 변경하지 않는다.

Knowledge entry는 고유 ID를 가져야 하며 정확히 하나의 `required-promotion-review` 조건에 연결된다. 실제 catalog의 reviewer, evidence reference, effective/expiry 기간이 그 조건과 일치하지 않거나 entry가 아직 `draft`이면 readiness는 실패한다. Evaluation fixture의 통과만으로 실제 catalog review를 대신할 수 없다.

각 Foundation Decision과 release Evidence는 canonical JSON record digest를 포함한다. Decision은 `previous_decision_digest`로 직전 레코드에 연결되고 기록 시각도 엄격히 증가해야 하므로, 배열 순서를 바꿔 과거 승인을 최신 승인처럼 복원할 수 없다. readiness는 최신 승인 결박뿐 아니라 과거 Decision 전체의 digest chain, 시각 순서와 중복 ID도 검사한다. 따라서 기록된 거부 결과, 승인 주체, Evidence 결과나 provenance가 파일 편집으로 달라지면 새 digest와 정식 기록 절차 없이는 promotion할 수 없다. 첫 Decision은 `previous_decision_digest: null`로 시작한다. `approval_digest` 도입 전의 legacy Decision은 당시 존재하던 필드 전체를 record digest로 고정하되, 존재하지 않았던 approval field를 소급 생성하지 않는다.

`release-check`는 승인 여부를 자동으로 결정하지 않고 현재 blocker를 보고한다. `foundation-promote`는 사람의 명시적 승인 이후에만 실행하는 쓰기 명령이다.

현재 공통 기준선은 `isekai-foundation@0.1.0`이며 최신 Foundation Decision `DEC-FND-20260806051711261003`과 digest-bound passing Evidence를 근거로 release와 등록된 21개 asset이 `approved` 상태다. 후속 gap은 approved v0.1.0을 임의 수정하지 않고 patch/minor Foundation version으로 보완한다. API 사용 시 `plan_foundation_promotion(root)`은 release manifest와 등록 asset 21개를 합친 22개 target의 상대 path·version·from/to status를 결정적으로 반환한다. `promote_foundation(root, dry_run=True)`는 같은 plan과 blocker만 보고하며 JSON, mode, Decision, Evidence를 변경하지 않는다.

실행 promotion은 모든 22개 JSON 결과를 메모리에서 만들고 `load_foundation` preflight와 readiness를 통과시킨 뒤 시작한다. 각 target은 같은 directory의 temporary file에 write·flush·fsync하고, 전체 staging 성공 후 `os.replace` commit한다. commit 또는 postflight(load, 22개 approved, readiness) 중 예외가 나면 원본 bytes와 mode를 복원하고 temporary file을 삭제한다. 이 transaction은 단일 프로세스·로컬 파일시스템 경계의 best-effort rollback이며 전원 손실, 파일시스템/외부 프로세스의 동시 변경, rollback 자체의 I/O 실패까지 원자성을 보장하지 않는다. descriptor의 중복·절대/상위 경로와 release.json 충돌은 preflight에서 차단한다. 기존 `promote_foundation(root)` 호출은 호환성을 위해 실행 모드로 유지한다.
