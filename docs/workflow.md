# Workflow

- 설명: Query·Quick Change·Unit 작업 라우팅과 AWS형 AI-DLC 수명주기
- 문서 역할: [ISEKAI canonical 문서 집합](isekai.md)의 일부

## 작업 라우팅

모든 질문과 변경에 정식 AI-DLC를 적용하지 않는다. 작업의 지속성·위험·불확실성·협업 필요성에 따라 세 경로로 나눈다.

### Query

설명·조회·요약·읽기 전용 분석·아이디어 비교는 바로 처리한다.

```text
질문 → 필요한 정보 확인 → 답변
```

별도 Unit과 영속 산출물은 만들지 않는다.

### Quick Change

작고 명확하며 저위험이고 쉽게 되돌릴 수 있는 변경이다.

```text
의도 확인 → 최소 변경 → 관련 검증 → 결과 기록
```

오타, 작은 문서 수정, 단일 파일의 명백한 버그와 기존 동작을 바꾸지 않는 정리가 해당한다. 정식 Inception 문서는 만들지 않지만 변경과 검증 결과는 남긴다.

### Unit of Work

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

### Goal/Direct Request Intake

Host의 `/goal` 결과와 사용자의 직접 요청은 별도 lifecycle로 만들지 않고 동일한 Normalized Intent로 변환한다.

```text
/goal 또는 직접 요청
→ Goal, Expected Outcome, Scope, Constraints, Acceptance Criteria 정규화
→ Query / Quick Change / Unit 라우팅
```

Query는 Unit을 만들지 않고 답변한다. Quick Change는 최소 변경과 검증만 남긴다. Unit은 정규화된 Intent를 `unit.json`과 `intent.md`에 보존하고 Inception부터 AI-DLC를 시작한다. Goal은 별도 Agent나 Goal Engine이 아니라 AI-DLC의 입력 방식이다.

## AWS형 AI-DLC

AWS AI-DLC의 핵심 모델을 유지한다.

```text
AI가 계획·질문·선택지를 제시
→ 사람이 의도·범위·중요 결정을 검증
→ AI가 승인된 계획을 실행
→ 자동 평가와 사람이 결과를 검토
→ 산출물·결정·증거를 다음 단계에 영속화
```

### Inception

사업·보안 의도를 검증 가능한 Unit으로 바꾼다.

AI는 자료를 탐색하고 명확화 질문, 요구사항, 비목표, 위험과 인수 조건을 제안한다. 사람은 문제, 기대 성과, 범위, 우선순위, 제약과 Construction 진입을 결정한다.

필수 결과는 Intent, Requirements, Decisions, Acceptance Criteria와 Risk다.

### Construction

승인된 의도를 제품 변화로 만든다.

AI는 아키텍처·도메인·Semantic 변경, 구현 계획, 코드, 테스트, 문서와 평가를 제안·작성한다. 사람은 중요한 아키텍처, 외부 계약, 권한, 마이그레이션과 Release 진입을 결정한다.

필수 결과는 Architecture, Plan, Code, Tests, Evaluations, Evidence와 Release Decision이다.

### Operations

안전하게 배포하고 실제 결과를 다음 Unit에 반영한다.

AI는 배포·운영 절차, 관측 결과, 장애·사용자 피드백과 개선 Unit을 제안한다. 사람은 운영 배포, 롤백, 고위험 실행, 사고 대응과 공식 지식 승격을 결정한다.

필수 결과는 Release Evidence, Deployment Record, Operational Feedback, Incident/Lesson과 Next Unit이다.

### 공통 반복 루프

```text
Plan → Clarify → Human Decision → Execute → Verify → Persist
```

AI는 승인되지 않은 중요한 결정을 암묵적으로 대신하지 않는다.
