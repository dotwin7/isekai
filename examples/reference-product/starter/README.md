# Reference Product 시작 상태

이 작은 Python 제품은 ISEKAI 프로젝트 로컬 Runtime E2E 테스트에서
사용하는 결정적인 시작 상태다. `FeatureProposal` 레코드를 포함한 제품
backlog를 모델링한다.

E2E Unit은 다음 인수 조건에 따라 제안 우선순위 기능을 추가한다.

- `high` 영향도 제안이 `medium`, `low` 영향도보다 먼저 온다.
- 영향도가 같으면 제안 ID 순으로 정렬한다.
- 잘못된 영향도를 거부한다.
- 입력 제안 레코드를 변경하지 않는다.

제품은 의도적으로 Python 표준 라이브러리만 사용한다. 따라서 제품 의존성을
다운로드하지 않고 ISEKAI 설치와 workflow 동작을 검증할 수 있다.
