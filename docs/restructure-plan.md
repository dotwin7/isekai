# ISEKAI 구조 개편 계획

isekai → isekai-core + isekai-foundation + isekai-presets 3저장소 분리

## 저장소 구성

### dotwin7/isekai-core

거버넌스 엔진 + 프리셋 로더 + 디스패처. 콘텐츠 없음.

포함: `runtime/`, `workflow/`, `dispatch/`, `distribution/`, `mcp_server.py`, `cli/`, `adapters/`

### dotwin7/isekai-foundation

조직 공통 거버넌스 규칙. gate-matrix, 계약, 평가 기준, 프로필.

포함: `core/schema.json`, `governance/`, `evaluations/`, `domains/`, `knowledge/`, `semantics/`, `decisions.json`, `evidence/`

### dotwin7/isekai-presets

프리셋 컬렉션. 각 프리셋 = phases + skills + rules + knowledge + dispatch config.

포함: `default/`, `system-maintenance/`, `security-ops/`, `migration/`, `greenfield/`

## 이관 대상

| 현재 위치 (isekai) | 이관 대상 | 이유 |
|---|---|---|
| `foundation/` | `isekai-foundation/` | 여러 프로젝트가 공유, 독립 버전 관리 필요 |
| `catalog/` | `isekai-presets/default/` | manifest, skills, phases 전부 프리셋의 일부 |
| dispatch config defaults | `isekai-presets/default/dispatch.json` | 모델 선택은 시간/환경에 따라 변경 |

### isekai-core에 남는 것

| 모듈 | 역할 |
|---|---|
| `src/isekai/runtime/` | Phase 강제, action dispatch, MCP gateway |
| `src/isekai/workflow/` | Session, binding, catalog 로더, project discovery |
| `src/isekai/catalog/ai_dlc/` | AI-DLC lifecycle 컨트롤러 코드 |
| `src/isekai/catalog/agent_control/` | Agent Control 컨트롤러 코드 |
| `src/isekai/dispatch/` | 디스패처 루프, 브로커, 러너 |
| `src/isekai/distribution/` | 설치 + 프리셋 로더 (추가 예정) |
| `src/isekai/mcp_server.py` | Core MCP 서버 |
| `runtime/adapters/` | 호스트별 Runtime Skill 템플릿 |

## 설치 흐름

```bash
# 1. 새 프로젝트 디렉터리 생성
mkdir my-project && cd my-project

# 2. 설치 — 3개 소스에서 각각 가져옴
isekai install \
  --source dotwin7/isekai-core --ref v0.5.0 \
  --foundation dotwin7/isekai-foundation --ref v0.3.0 \
  --preset dotwin7/isekai-presets --type system-maintenance \
  --path . --runtime claude --init

# 3. 로더가 하는 일:
#    a) isekai-core에서 엔진 → .isekai/runtime/
#    b) isekai-foundation에서 Foundation → foundation/
#    c) isekai-presets/system-maintenance에서 → .isekai/preset/
#       (skills, phases, rules, knowledge, dispatch config)
#    d) Runtime Skill 배치 → .claude/skills/isekai/

# 4. 결과 프로젝트 구조
my-project/
├── .isekai/
│   ├── bin/                  런처
│   ├── runtime/              Core 엔진
│   ├── catalog/              Catalog manifest
│   └── preset/               프리셋 콘텐츠
│       ├── preset.json       phases, checks, dispatch
│       ├── skills/           단계별 스킬
│       ├── rules/            안전장치 규칙
│       └── knowledge/        도메인 지식
├── foundation/               Foundation 규칙
├── project.json              preset + foundation ref 기록
└── .claude/skills/isekai/    Runtime Skill

# 5. 작업 시작
isekai dispatch --project .
# 또는
claude → /isekai on
```

## 작업 순서

### v0.4.0 — 완료

Phase 계약 + 스킬 + 디스패처. Phase allowed_actions/checks 강제, 5 stage 스킬, 디스패처 루프, Human Gate 처리, 에이전트 교체. 현재 master에 태그 완료.

### v0.5.0 — 저장소 분리 + 프리셋 로더

1. dotwin7/isekai-core 저장소 생성 (isekai rename)
2. dotwin7/isekai-foundation 저장소 생성, foundation/ 이관
3. dotwin7/isekai-presets 저장소 생성, skills/phases/dispatch defaults 이관
4. Core에 프리셋 로더 구현 (Git 소스에서 프리셋 fetch → .isekai/preset/에 배치)
5. install CLI에 --preset, --foundation 인자 추가
6. Core가 .isekai/preset/에서 phases/skills/checks를 읽도록 변경
7. 기본 프리셋(default) 작성 — 현재 하드코딩된 콘텐츠를 프리셋 형식으로 추출

### v0.6.0 — Knowledge on-demand + 프리셋 확장

Knowledge 키워드 매칭 로딩. system-maintenance 프리셋 작성 (LG CNS 사례 기반). security-ops 프리셋 작성 (nahonza-agents 연동).

### v0.7.0 — 자동화 스킬 + 외부 연동

산출물 게시, 문서 변환, Jira/Confluence MCP 연동. 디스패처 스킬 파이프라인.

### v0.8~0.9 — meta-knowledge + 자기 개선

반복 교훈 감지. 스킬/규칙 자동 개선 제안. audit 로깅.

### v1.0 — 플랫폼 선언

프리셋 레지스트리 (중앙 허브). ACL + 버전 관리. 지식 DB 연동. 프리셋 3개 이상 실전 검증 후 공개.

## 의존 관계

- isekai-core ← 로드 ← isekai-foundation
- isekai-core ← 로드 ← isekai-presets
- isekai-foundation ↔ 독립 ↔ isekai-presets

Core는 Foundation과 Presets를 설치 시점에 로드합니다. Foundation과 Presets는 서로 의존하지 않습니다. 세 저장소 모두 독립 버전 관리됩니다.

## 원칙

isekai-core에는 콘텐츠가 없습니다. 거버넌스 규칙(Foundation), 워크플로우 구성(Presets), 도메인 지식(Knowledge)은 전부 외부 소스에서 로드됩니다. Core는 엔진이고, 나머지는 연료입니다.
