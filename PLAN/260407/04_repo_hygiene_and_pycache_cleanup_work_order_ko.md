# Step 4 상세 실행 작업지시서 — pycache / Repo Hygiene 정리

## 1. 목적
저장소를 더 협업 친화적이고 CI 친화적인 상태로 정리한다.

이 단계는 기능 개발보다 품질/운영성 보강이다.
현재 repo는 README/ARCHITECTURE/CHANGELOG까지는 좋아졌지만,
tracked cache artifact와 개발 품질 도구 정리가 더 필요하다.

---

## 2. 목표
1. tracked `__pycache__`를 repo에서 제거한다.
2. `.gitignore`를 보강해 재발을 막는다.
3. dev tooling(ruff, mypy, pytest-cov 등) 도입 여부를 정리한다.
4. repo description/topics 같은 외부 discoverability 정보도 정리한다.

---

## 3. 현재 상태 요약
- 루트 문서(README/ARCHITECTURE/CHANGELOG)는 좋아졌다.
- 하지만 repo tree에 `__pycache__`가 여전히 보인다.
- `pyproject.toml` 기준 dev tooling은 아직 비교적 얇다.

---

## 4. 수정 대상 파일

### 핵심 파일
- `.gitignore`
- `pyproject.toml`
- Git 작업 자체 (`git rm -r --cached`)
- `.github/workflows/` (선택)
- README/ARCHITECTURE/HANDOFF cross-link 점검

### 선택적 신규 파일
- `.github/workflows/ci.yml`
- `Makefile` 또는 `scripts/lint.sh`

---

## 5. 상세 구현 지시

### 5-1. tracked `__pycache__` 제거
#### 실행 작업
```bash
git rm -r --cached src/**/__pycache__ tests/**/__pycache__
```

또는 실제 tracked 경로에 맞춰 제거.

#### 목적
repo diff 노이즈 제거.

---

### 5-2. `.gitignore` 보강
#### 추가 항목 예
```gitignore
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/
```

#### 목적
cache artifact 재유입 방지.

---

### 5-3. `pyproject.toml` dev tooling 정리
#### 권장 추가
- `ruff`
- `mypy`
- `pytest-cov`

#### 예시
```toml
[project.optional-dependencies]
dev = [
  "pytest",
  "pytest-asyncio",
  "pytest-cov",
  "ruff",
  "mypy"
]
```

#### 목적
정적 품질 보강.

---

### 5-4. CI workflow 추가 또는 보강
#### 신규 파일
- `.github/workflows/ci.yml`

#### 최소 단계
- install
- lint
- type check
- unit tests

#### 목적
새 PR/커밋에서 contract drift 조기 발견.

---

### 5-5. README / ARCHITECTURE / CHANGELOG cross-link 점검
#### 해야 할 일
README에서
- ARCHITECTURE
- HANDOFF
- mockdata/README
- 주요 실행 경로
를 명시적으로 링크

ARCHITECTURE에서도
- README와 mockdata/README 참조 링크 점검

#### 목적
새 참여자가 빠르게 구조를 이해하게 한다.

---

## 6. Acceptance Criteria
1. repo에 tracked `__pycache__`가 없다.
2. `.gitignore`에 Python cache/tooling 산출물이 모두 반영된다.
3. dev tooling 설치/실행 경로가 `pyproject.toml`에 정리된다.
4. 최소한 로컬 또는 CI에서 lint/test/type-check 경로가 준비된다.

---

## 7. 테스트/검증 항목
- `git status`에서 cache artifact 재생성 후 untracked ignore 확인
- `pip install -e ".[dev]"` 후 lint/test/type-check smoke run
- CI workflow dry run (가능하면)

---

## 8. 완료 후 검토 포인트
- repo About(description/topics/website) 채우기
- CONTRIBUTING.md 추가 여부 검토
