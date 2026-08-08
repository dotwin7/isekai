# 아키텍처

`reference_product.proposals`에 순수 도메인 함수 하나를 추가한다. 기존
정규화 경계를 재사용하고 복사된 dictionary를 반환하며, 저장소와 전송 계층에
의존하지 않도록 기능을 유지한다.
