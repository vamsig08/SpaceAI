# Testing Standards & Coverage Strategy

**Reference:** Engineering Standards (80%+ coverage), Development Rules (never skip tests)  
**Date:** 2026-06-23

---

## 1. Testing Philosophy

- **Every feature ships with tests.** No exceptions.
- **Tests are production-quality code.** Same standards as application code: type hints, no TODOs, proper error handling.
- **Tests document behavior.** Test names describe the expected behavior in plain English.
- **Fast feedback loop.** Unit tests run in under 10 seconds. Full suite in under 60 seconds.
- **No mocks unless explicitly requested.** Prefer real implementations with test databases.

---

## 2. Test Pyramid

```
                 +---------+
                 |   E2E   |  (Future: Playwright)
                 |  ~5%    |
                 +---------+
              +--+Integrat.+--+
              |  |  ~25%   |  |
              |  +---------+  |
           +--+  |  Unit   |  +--+
           |  |  |  ~70%   |  |  |
           |  |  +---------+  |  |
           |  +---------------+  |
           +---------------------+
```

| Layer | Coverage Target | Speed | What is Tested |
|-------|----------------|-------|---------------|
| Unit | 70% of tests | under 10s total | Services, repositories (with test DB), utilities, AI prompts |
| Integration | 25% of tests | under 45s total | API routes end-to-end, background tasks, SSE streams |
| E2E | 5% of tests | under 120s total | Critical user flows (scan, view results, cleanup) |

---

## 3. Backend Testing

### 3.1 Framework and Tools

| Tool | Purpose |
|------|---------|
| `pytest` | Test runner, fixtures, parametrize |
| `pytest-asyncio` | Async test support |
| `pytest-cov` | Coverage reporting |
| `httpx` | AsyncClient for API testing |
| `factory-boy` | Test data factories |
| `time-machine` | Deterministic time control |

### 3.2 Test Structure

```
backend/tests/
├── conftest.py                    # Shared fixtures (test DB, client, factories)
├── unit/
│   ├── services/
│   │   ├── test_scanner_service.py
│   │   ├── test_analytics_service.py
│   │   ├── test_duplicate_service.py
│   │   ├── test_stale_file_service.py
│   │   ├── test_workspace_service.py
│   │   ├── test_recommendation_service.py
│   │   ├── test_prediction_service.py
│   │   ├── test_cleanup_service.py
│   │   └── test_audit_service.py
│   ├── repositories/
│   │   ├── test_file_repository.py
│   │   ├── test_scan_repository.py
│   │   ├── test_duplicate_repository.py
│   │   └── test_recommendation_repository.py
│   ├── scanner/
│   │   ├── test_crawler.py
│   │   ├── test_hasher.py
│   │   ├── test_exclusions.py
│   │   ├── test_batch_writer.py
│   │   ├── test_workspace_detector.py
│   │   └── test_platform.py
│   ├── ai/
│   │   ├── test_openai_provider.py
│   │   ├── test_ollama_provider.py
│   │   ├── test_circuit_breaker.py
│   │   └── test_prompts.py
│   └── core/
│       ├── test_config.py
│       └── test_exceptions.py
├── integration/
│   ├── api/
│   │   ├── test_scan_endpoints.py
│   │   ├── test_file_endpoints.py
│   │   ├── test_duplicate_endpoints.py
│   │   ├── test_analytics_endpoints.py
│   │   ├── test_recommendation_endpoints.py
│   │   ├── test_cleanup_endpoints.py
│   │   └── test_health_endpoints.py
│   ├── workers/
│   │   ├── test_scan_worker.py
│   │   ├── test_hash_worker.py
│   │   └── test_task_manager.py
│   └── test_sse_streaming.py
└── fixtures/
    ├── sample_filesystem/          # Temp directory structures for scanner tests
    ├── mock_ai_responses/          # Canned AI provider responses
    └── factories.py                # factory-boy model factories
```

### 3.3 Key Fixtures

```python
# conftest.py

@pytest.fixture
async def db_session():
    """Create a fresh in-memory SQLite database for each test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async with AsyncSession(engine) as session:
        yield session
    
    await engine.dispose()


@pytest.fixture
async def api_client(db_session):
    """Configured test client with test database."""
    app.dependency_overrides[get_db_session] = lambda: db_session
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def temp_filesystem(tmp_path):
    """Create a temporary directory structure for scanner tests."""
    # Creates realistic file trees with known sizes and timestamps
    ...


@pytest.fixture
def scan_factory(db_session):
    """Factory for creating scan records in test DB."""
    ...
```

### 3.4 Test Naming Convention

```python
# Pattern: test_{method}_{scenario}_{expected_result}

class TestScannerService:
    async def test_start_scan_with_valid_path_creates_scan_record(self): ...
    async def test_start_scan_with_nonexistent_path_raises_not_found(self): ...
    async def test_start_scan_when_scan_already_running_raises_conflict(self): ...
    async def test_cancel_scan_sets_status_to_cancelled(self): ...
    async def test_resume_scan_from_checkpoint_skips_processed_dirs(self): ...
```

### 3.5 Repository Tests (Real DB, Not Mocked)

Per the development rules ("never use mock implementations unless explicitly requested"), repository tests use real SQLite in-memory databases:

```python
class TestFileRepository:
    async def test_create_batch_inserts_all_records(self, db_session):
        repo = FileRepository(db_session)
        files = [FileFactory.build() for _ in range(1000)]
        
        await repo.create_batch(files)
        
        count = await repo.count(scan_id=files[0].scan_id)
        assert count == 1000
    
    async def test_find_largest_returns_top_n_by_size(self, db_session):
        repo = FileRepository(db_session)
        # Insert files with known sizes
        ...
        
        result = await repo.find_largest(limit=10)
        
        assert len(result) == 10
        assert result[0].size_bytes >= result[1].size_bytes  # Sorted desc
```

### 3.6 Service Tests (Real Repos, Test DB)

Services are tested with real repositories against test databases. External dependencies (AI providers, filesystem) are the ONLY things stubbed:

```python
class TestDuplicateService:
    async def test_detect_duplicates_groups_files_with_same_hash(self, db_session):
        # Setup: insert files with known hashes
        file_repo = FileRepository(db_session)
        dup_repo = DuplicateRepository(db_session)
        service = DuplicateService(file_repo, dup_repo)
        
        # Act
        groups = await service.detect_duplicates(scan_id="test-scan-id")
        
        # Assert
        assert len(groups) == 2
        assert groups[0].wasted_bytes == 1024 * 3  # 3 copies * 1KB
```

### 3.7 Integration Tests (Full HTTP Cycle)

```python
class TestScanEndpoints:
    async def test_create_scan_returns_202_with_scan_id(self, api_client):
        response = await api_client.post("/api/v1/scans", json={
            "root_path": "/tmp/test-scan",
            "scan_type": "full",
        })
        
        assert response.status_code == 202
        data = response.json()["data"]
        assert "id" in data
        assert data["status"] == "pending"
    
    async def test_create_scan_with_invalid_path_returns_422(self, api_client):
        response = await api_client.post("/api/v1/scans", json={
            "root_path": "",
            "scan_type": "full",
        })
        
        assert response.status_code == 422
```

---

## 4. Frontend Testing

### 4.1 Framework and Tools

| Tool | Purpose |
|------|---------|
| `vitest` | Test runner (fast, ESM-native) |
| `@testing-library/react` | Component testing |
| `@testing-library/user-event` | User interaction simulation |
| `msw` (Mock Service Worker) | API mocking at network level |

### 4.2 Test Structure

```
frontend/tests/
├── components/
│   ├── ui/
│   │   ├── button.test.tsx
│   │   ├── data-table.test.tsx
│   │   └── progress.test.tsx
│   ├── scan/
│   │   ├── scan-trigger.test.tsx
│   │   └── scan-progress.test.tsx
│   ├── charts/
│   │   └── storage-pie-chart.test.tsx
│   └── cleanup/
│       └── cleanup-confirmation.test.tsx
├── hooks/
│   ├── use-scan-progress.test.ts
│   ├── use-api.test.ts
│   └── use-pagination.test.ts
├── lib/
│   ├── api-client.test.ts
│   └── format.test.ts
├── mocks/
│   ├── handlers.ts              # MSW request handlers
│   └── server.ts                # MSW server setup
└── setup.ts                     # Test environment setup
```

### 4.3 Component Test Pattern

```typescript
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ScanTrigger } from '@/components/scan/scan-trigger';

describe('ScanTrigger', () => {
  it('shows scan button in idle state', () => {
    render(<ScanTrigger />);
    expect(screen.getByRole('button', { name: /start scan/i })).toBeInTheDocument();
  });
  
  it('disables button when scan is in progress', () => {
    render(<ScanTrigger scanStatus="running" />);
    expect(screen.getByRole('button', { name: /scanning/i })).toBeDisabled();
  });
  
  it('calls onStartScan with path when button clicked', async () => {
    const onStartScan = vi.fn();
    render(<ScanTrigger onStartScan={onStartScan} />);
    
    await userEvent.click(screen.getByRole('button', { name: /start scan/i }));
    
    expect(onStartScan).toHaveBeenCalledWith(expect.objectContaining({
      root_path: expect.any(String),
    }));
  });
});
```

---

## 5. Coverage Strategy

### 5.1 Coverage Targets

| Layer | Target | Enforcement |
|-------|--------|-------------|
| Backend Services | 90% | `pytest-cov --fail-under=90` on `app/services/` |
| Backend Repositories | 85% | `pytest-cov --fail-under=85` on `app/repositories/` |
| Backend Scanner | 85% | `pytest-cov --fail-under=85` on `app/scanner/` |
| Backend AI | 80% | `pytest-cov --fail-under=80` on `app/ai/` |
| Backend API Routes | 75% | `pytest-cov --fail-under=75` on `app/api/` |
| Backend Overall | 80% | `pytest-cov --fail-under=80` |
| Frontend Components | 75% | vitest coverage threshold 75 |
| Frontend Hooks | 85% | Higher because hooks contain logic |
| Frontend Overall | 80% | Matches NFR requirement |

### 5.2 What Counts Toward Coverage

- Lines executed during tests.
- Branch coverage (both sides of if/else).
- NOT: type stubs, Pydantic model definitions, Alembic migrations.

### 5.3 Exclusions from Coverage

```ini
# pyproject.toml
[tool.coverage.run]
omit = [
    "*/alembic/*",
    "*/tests/*",
    "*/__init__.py",
    "*/conftest.py",
]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "if TYPE_CHECKING:",
    "if __name__ == .__main__.:",
    "@overload",
]
```

---

## 6. Test Data Strategy

### 6.1 Factories (Backend)

```python
# tests/fixtures/factories.py
import factory
from app.models.file import File
from app.models.scan import Scan

class ScanFactory(factory.Factory):
    class Meta:
        model = Scan
    
    id = factory.LazyFunction(lambda: str(uuid.uuid4()))
    root_path = "/tmp/test-scan"
    status = "completed"
    scan_type = "full"
    total_files = 1000
    total_dirs = 50
    total_size_bytes = 1073741824  # 1 GB

class FileFactory(factory.Factory):
    class Meta:
        model = File
    
    id = factory.LazyFunction(lambda: str(uuid.uuid4()))
    scan_id = factory.LazyFunction(lambda: str(uuid.uuid4()))
    path = factory.Sequence(lambda n: f"/tmp/test/file_{n}.txt")
    directory = "/tmp/test"
    filename = factory.Sequence(lambda n: f"file_{n}.txt")
    extension = ".txt"
    size_bytes = factory.Faker("random_int", min=100, max=1000000)
    category = "document"
```

### 6.2 Temporary Filesystems (Scanner Tests)

```python
@pytest.fixture
def realistic_filesystem(tmp_path):
    """Create a filesystem mimicking a developer machine."""
    
    # Python project
    project = tmp_path / "projects" / "myapp"
    project.mkdir(parents=True)
    (project / "main.py").write_text("print('hello')")
    (project / "__pycache__").mkdir()
    (project / "__pycache__" / "main.cpython-312.pyc").write_bytes(b"\x00" * 1024)
    
    # Node project
    node_proj = tmp_path / "projects" / "webapp"
    node_proj.mkdir(parents=True)
    (node_proj / "package.json").write_text('{"name": "webapp"}')
    node_modules = node_proj / "node_modules"
    node_modules.mkdir()
    # Simulate large node_modules
    for i in range(100):
        (node_modules / f"pkg_{i}" / "index.js").parent.mkdir(parents=True)
        (node_modules / f"pkg_{i}" / "index.js").write_text(f"module {i}")
    
    return tmp_path
```

### 6.3 MSW Handlers (Frontend)

```typescript
// tests/mocks/handlers.ts
import { http, HttpResponse } from 'msw';

export const handlers = [
  http.get('/api/v1/analytics/overview', () => {
    return HttpResponse.json({
      data: {
        total_storage: 500_107_862_016,
        used_storage: 387_028_092_928,
        free_storage: 113_079_769_088,
        file_count: 892_341,
        recovery_opportunities: 58_982_058_496,
      }
    });
  }),
  
  http.post('/api/v1/scans', () => {
    return HttpResponse.json({
      data: { id: 'test-scan-id', status: 'pending' }
    }, { status: 202 });
  }),
];
```

---

## 7. CI Integration

### 7.1 Test Pipeline (Conceptual)

```yaml
test-backend:
  steps:
    - pip install -e ".[dev]"
    - pytest --cov=app --cov-report=xml --cov-fail-under=80
    - mypy app/

test-frontend:
  steps:
    - npm ci
    - npm run test -- --coverage
    - npm run lint
    - npm run type-check
```

### 7.2 Pre-Commit Checks

```
# Run before every commit:
- ruff check (lint)
- ruff format --check (format)
- mypy (type check)
- pytest tests/unit/ (fast unit tests only)
```

### 7.3 Test Execution Commands

```bash
# Backend
pytest                                    # Run all tests
pytest tests/unit/                        # Unit tests only (fast)
pytest tests/integration/                 # Integration tests only
pytest --cov=app --cov-report=html        # With coverage report
pytest -x                                 # Stop on first failure
pytest -k "test_scanner"                  # Run matching tests

# Frontend
npm run test                              # Run all tests (vitest --run)
npm run test:coverage                     # With coverage
npm run test:watch                        # Watch mode (dev only, not CI)
```

---

## 8. Testing Anti-Patterns (Forbidden)

| Anti-Pattern | Why It Is Banned |
|--------------|-----------------|
| `@pytest.mark.skip("TODO")` | Development rules: never use TODO placeholders |
| `mock.patch` on repository internals | Test with real test DB instead |
| Tests that depend on execution order | Use fixtures for setup/teardown |
| Tests that hit external services | Use factories/MSW for external data |
| Tests without assertions | Every test must assert something meaningful |
| Sleeping (`time.sleep`) in tests | Use async wait/poll patterns instead |
| Testing implementation details | Test behavior (inputs to outputs), not internal state |
