# SpaceAI

**AI-powered storage intelligence for developers.**

SpaceAI scans your filesystem, detects waste, predicts growth, and helps you safely reclaim disk space — all from a single dashboard.

---

## Why SpaceAI?

Every developer eventually faces: *"My disk is full. What's using all this space?"*

SpaceAI answers that question with intelligence:

- **Scans 1M+ files in under 15 minutes** with multi-threaded I/O
- **Detects duplicates** using SHA-256 verification (zero false positives)
- **Identifies stale files** you haven't touched in months
- **Finds developer waste** — node_modules, .venv, build artifacts, ML checkpoints
- **Generates recommendations** with confidence scores and risk levels
- **Predicts disk exhaustion** using linear regression on historical data
- **Safely cleans up** with trash-first approach and instant rollback

Nothing is ever permanently deleted. Every action is reversible.

---

## Key Features

| Feature | Description |
|---------|-------------|
| **Filesystem Scanner** | High-performance BFS traversal with checkpoint recovery |
| **Duplicate Detection** | 3-stage pipeline: size grouping → partial hash → full SHA-256 |
| **Stale Analysis** | Sigmoid-based scoring with configurable thresholds |
| **Workspace Optimizer** | Detects Python, Node, Java, Rust, ML, Docker, IDE artifacts |
| **Recommendation Engine** | Deterministic rules + optional AI enrichment (Ollama/OpenAI) |
| **Storage Forecasting** | Linear regression, moving average, exponential smoothing |
| **Safety Framework** | Propose → Approve → Execute → Rollback with audit trail |
| **Real-time Progress** | Server-Sent Events for live scan monitoring |

---

## Architecture

```
┌─────────────────────────────────────────┐
│            Next.js Frontend              │
│  React · TypeScript · Tailwind · SSE    │
└───────────────────┬─────────────────────┘
                    │ HTTP / SSE
┌───────────────────▼─────────────────────┐
│            FastAPI Backend               │
│                                          │
│  ┌──────────┐ ┌──────────┐ ┌────────┐  │
│  │  Scanner │ │ Analysis │ │Cleanup │  │
│  │  Engine  │ │ Pipeline │ │ Safety │  │
│  └────┬─────┘ └────┬─────┘ └───┬────┘  │
│       │             │           │        │
│  ┌────▼─────────────▼───────────▼────┐  │
│  │         SQLite (WAL mode)          │  │
│  └────────────────────────────────────┘  │
└──────────────────────────────────────────┘
```

---

## Technology Stack

**Backend:**
- Python 3.12+ / FastAPI / SQLAlchemy / SQLite (WAL)
- Pydantic / structlog / Alembic / asyncio

**Frontend:**
- Next.js 15 / React 19 / TypeScript / Tailwind CSS
- React Query / Recharts / Lucide Icons

**AI Layer (optional):**
- Ollama (local) / OpenAI-compatible APIs
- Rule-based engine works fully offline

---

## Screenshots

*Coming soon — the dashboard features dark mode, AI-powered insights, storage health visualization, and one-click cleanup.*

---

## Installation

### Prerequisites

- Python 3.12+
- Node.js 20+
- npm

### Backend Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
mkdir -p data
alembic upgrade head
```

### Frontend Setup

```bash
cd frontend
npm install
```

### Running Locally

**Backend** (terminal 1):
```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**Frontend** (terminal 2):
```bash
cd frontend
npm run dev
```

Open http://localhost:3000

---

## Docker Support

*Docker Compose configuration coming in v0.2.0.*

```bash
# Future:
docker compose up
```

---

## Project Structure

```
SpaceAI/
├── backend/
│   ├── app/
│   │   ├── api/v1/          # REST endpoints
│   │   ├── services/        # Business logic
│   │   ├── repositories/    # Data access
│   │   ├── models/          # SQLAlchemy ORM
│   │   ├── schemas/         # Pydantic models
│   │   ├── scanner/         # Filesystem engine
│   │   ├── workers/         # Background tasks
│   │   └── ai/              # AI provider abstraction
│   ├── tests/               # 391 tests, 87% coverage
│   ├── alembic/             # Database migrations
│   └── benchmarks/          # Performance suite
├── frontend/
│   ├── app/                 # Next.js pages
│   ├── components/          # React components
│   └── lib/                 # Utilities & API client
└── docs/                    # Architecture decisions & specs
```

---

## Testing

```bash
# Backend
cd backend
source .venv/bin/activate
pytest                        # Run all 391 tests
pytest --cov=app              # With coverage (87%+)
make lint                     # Ruff linting
make typecheck                # Mypy strict mode

# Frontend
cd frontend
npx tsc --noEmit              # Type checking
npm run build                 # Production build
```

---

## Roadmap

- [x] Filesystem scanner (1M+ files)
- [x] Duplicate detection (SHA-256)
- [x] Stale file analysis
- [x] Developer workspace optimizer
- [x] Recommendation engine
- [x] Storage forecasting
- [x] Safety framework (trash + rollback)
- [x] Next.js dashboard
- [x] SSE real-time progress
- [ ] Docker Compose deployment
- [ ] Recharts visualizations
- [ ] Multi-platform CI/CD
- [ ] Browser-based file picker
- [ ] Scheduled scans
- [ ] NAS/external drive support

---

## Known Limitations

- **Disk space requirement:** Scanning 1M files creates a ~1 GB database. Ensure sufficient free space.
- **macOS atime:** Stale detection uses modification time (atime unreliable on APFS).
- **No permanent delete:** By design — all cleanup goes to trash first.
- **Single user:** Currently designed for local-first single-user operation.
- **Forecast needs history:** Predictions require multiple scans over multiple days.

---

## Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

## Acknowledgments

Built with FastAPI, Next.js, SQLAlchemy, and Python's incredible async ecosystem.

Designed to demonstrate production engineering practices for storage analysis, predictive analytics, and safe automation.
