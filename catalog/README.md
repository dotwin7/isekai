# ISEKAI Catalog

이 디렉터리는 ISEKAI Runtime이 제공하는 모든 Catalog entry의 배포 원본이다.

## 구조

```text
catalog/
├── catalog.json                       source catalog — 배포할 entry 목록
├── README.md
├── _template/
│   └── manifest.json.example          새 Catalog entry manifest 템플릿
└── <entry-id>/
    └── <version>/
        ├── manifest.json              entry manifest (필수)
        ├── controller/                entry controller 코드
        ├── schemas/                   entry 전용 스키마
        ├── policies/                  entry 전용 정책
        ├── resources/                 정적 리소스
        ├── migrations/                버전 간 마이그레이션
        └── tests/                     entry 전용 테스트
```

## 현재 등록된 Entry

| ID | Version | Status | Delivery | 설명 |
|---|---|---|---|---|
| `ai-dlc` | 0.2.1 | active | core-bundled | Intake부터 Learn까지 거버넌스 개발주기 |

## 새 Entry 추가 절차

### 1. 디렉터리 생성

Entry ID는 `[a-z][a-z0-9-]{0,63}` 형식이다. 버전은 SemVer를 따른다.

```bash
mkdir -p catalog/<entry-id>/<version>
```

### 2. Entry manifest 작성

`_template/manifest.json.example`을 복사해 12개 필수 필드를 채운다.

```bash
cp catalog/_template/manifest.json.example \
   catalog/<entry-id>/<version>/manifest.json
```

필수 필드:

| 필드 | 설명 |
|---|---|
| `id` | entry ID, 디렉터리 이름과 일치 |
| `kind` | `"isekai-catalog-entry"` 고정 |
| `schema_version` | `"1.0.0"` |
| `version` | SemVer, 디렉터리 이름과 일치 |
| `status` | `"active"`, `"preview"`, `"deprecated"` 중 하나 |
| `title` | MCP resource 목록에 표시될 제목 |
| `description` | 한 줄 설명 |
| `control_protocol` | `"1.1.0"` (현재 Core 프로토콜) |
| `delivery` | `"core-bundled"` 또는 `"catalog-package"` |
| `actions` | 이 entry가 소유하는 Core action ID 목록 |
| `resources` | 이 entry가 제공하는 resource 이름 목록 |
| `authority` | `"cannot-expand-foundation-project-or-unit-authority"` 고정 |

### 3. Source catalog에 등록

`catalog.json`의 `entries` 배열에 항목을 추가한다. `manifest` 경로는 반드시 `<entry-id>/<version>/manifest.json` 형식이어야 한다.

```json
{
  "id": "<entry-id>",
  "version": "<version>",
  "manifest": "<entry-id>/<version>/manifest.json"
}
```

### 4. Distribution manifest 재생성

`catalog/` 하위에 파일이 추가되면 release component digest가 바뀐다.

```bash
uv run python -m isekai distribution-build --root .
uv run python -m isekai distribution-check --root .
```

### 5. 테스트 실행

```bash
uv run pytest tests/test_features.py -q
uv run pytest -q
```

## 제약

- Entry ID는 catalog 안에서 고유해야 한다.
- `authority`는 `"cannot-expand-foundation-project-or-unit-authority"` 고정이다. Catalog entry는 Foundation, Project Agent level, Unit Envelope, Human Gate를 확장할 수 없다.
- `preview` entry는 발견은 허용하되 실행 action을 제공하지 않는다.
- Catalog entry 업데이트로 기존 Unit이 새 기능이나 권한을 암묵적으로 얻지 않는다.
- `_`로 시작하는 디렉터리(예: `_template`)는 catalog에 등록하지 않는다.
