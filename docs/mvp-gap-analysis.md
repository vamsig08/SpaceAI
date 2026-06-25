# MVP Gap Analysis — Release Candidate Assessment

**Date:** 2026-06-24  
**Goal:** Identify all gaps between current implementation and shippable MVP

---

## 1. Classified Findings

### Critical (Blocks Release)

| # | Gap | Category | Impact |
|---|-----|----------|--------|
| C1 | **No scan initiation in frontend** | Missing workflow | Users cannot trigger a scan from the UI — the core user action is unavailable |
| C2 | **No SSE progress hook** | Missing feature | Scans run in background but user has no visibility into progress |
| C3 | **No Docker deployment** | Missing infra | NFR requires "Docker deployment in one command" — no Dockerfile or docker-compose exists |
| C4 | **No README with setup guide** | Missing docs | First-time users cannot install or run the application |

### High (Degrades Experience)

| # | Gap | Category | Impact |
|---|-----|----------|--------|
| H1 | Stale page shows hardcoded "—" values | Placeholder UI | Page looks broken when data exists |
| H2 | Workspaces page shows hardcoded "—" values | Placeholder UI | Same issue — no API integration |
| H3 | Duplicates page has empty `scanId` so queries never fire | Missing wiring | Data never loads even when available |
| H4 | No scan selector / context for which scan to display | Missing UX | Pages need to know which scan_id to query |
| H5 | No chart visualizations (Recharts installed but unused) | Missing feature | Dashboard lacks the visual appeal expected |
| H6 | Backend scan API route (`POST /api/v1/scans`) not implemented | Missing endpoint | Frontend can't trigger scans even if UI existed |

### Medium (Polish Issues)

| # | Gap | Category | Impact |
|---|-----|----------|--------|
| M1 | No mobile-responsive sidebar collapse | UI polish | Unusable on screens <768px |
| M2 | No error boundary / error UI component | UI resilience | API failures show blank pages |
| M3 | No `.env.local` / environment config guidance for frontend | DX | Developer needs to know API URL |
| M4 | No lint/format CI step | Quality gate | No automated quality enforcement |
| M5 | Recommendation page has no data integration | Incomplete | Shows placeholder instead of querying API |

### Low (Nice to Have for MVP)

| # | Gap | Category | Impact |
|---|-----|----------|--------|
| L1 | No frontend tests (vitest) | Testing | Types provide some safety, but no runtime tests |
| L2 | No dark/light mode toggle (hardcoded dark) | UI polish | Some users prefer light mode |
| L3 | No loading spinners on mutation buttons | UI polish | No visual feedback during API calls |
| L4 | No toast notifications | UI feedback | Actions complete silently |
| L5 | No Mermaid architecture diagrams | Docs | Referenced in architecture-review but not created |

---

## 2. Features Implemented But Not Exposed in UI

| Backend Feature | API Endpoint | UI Status |
|-----------------|-------------|-----------|
| Scan initiation | `POST /api/v1/scans` (not implemented) | No UI |
| Scan progress SSE | `GET /api/v1/scans/{id}/progress` | No UI |
| Duplicate detection trigger | `POST /api/v1/duplicates/detect` | No UI |
| Stale analysis trigger | `POST /api/v1/stale/analyze` | No UI |
| Workspace analysis trigger | `POST /api/v1/workspaces/analyze` | No UI |
| Recommendation generation | `POST /api/v1/recommendations/generate` | No UI |
| Forecast generation | `POST /api/v1/predictions/forecast` | No UI |
| Cleanup propose/approve/execute | `POST /api/v1/cleanup/*` | Displayed but no trigger buttons |
| Recommendation accept/dismiss | `PATCH /api/v1/recommendations/{id}` | No UI |
| Duplicate resolve (mark keeper) | `POST /api/v1/duplicates/{id}/resolve` | No UI |

---

## 3. Release Candidate Roadmap

### RC-1: Core Workflow (Critical blockers)

**Goal:** A user can scan, view results, and see progress.

| Task | Priority | Effort |
|------|----------|--------|
| Implement `POST /api/v1/scans` backend endpoint | Critical | 2h |
| Create scan initiation UI (path input + start button) | Critical | 2h |
| Implement SSE progress hook (`useScanProgress`) | Critical | 2h |
| Add scan progress overlay/modal with live stats | Critical | 2h |
| Add scan context selector (latest scan auto-selected) | High | 1h |
| Wire stale/workspaces/duplicates pages to use real scan_id | High | 1h |

**Outcome:** Full scan → view cycle works end-to-end.

### RC-2: Docker & Documentation

**Goal:** Anyone can run SpaceAI with one command.

| Task | Priority | Effort |
|------|----------|--------|
| Create `backend/Dockerfile` | Critical | 1h |
| Create `frontend/Dockerfile` | Critical | 1h |
| Create `docker-compose.yml` (backend + frontend) | Critical | 1h |
| Write root `README.md` with setup guide | Critical | 2h |
| Create `.env.example` at root level | Medium | 15m |

**Outcome:** `docker compose up` runs the full stack.

### RC-3: Data Integration & Visualization

**Goal:** All pages show real data with visual appeal.

| Task | Priority | Effort |
|------|----------|--------|
| Wire stale page to `GET /stale/summary` | High | 1h |
| Wire workspaces page to `GET /workspaces/summary` | High | 1h |
| Wire recommendations page to `GET /recommendations` | High | 1h |
| Add action buttons (trigger analysis, accept/dismiss) | High | 2h |
| Add Recharts storage pie chart (categories) | Medium | 1.5h |
| Add Recharts growth line chart (forecast page) | Medium | 1.5h |
| Fix duplicates page scanId wiring | High | 30m |

**Outcome:** All pages functional with real data.

### RC-4: Polish & Resilience

**Goal:** Production-quality user experience.

| Task | Priority | Effort |
|------|----------|--------|
| Add error boundary component | Medium | 1h |
| Add toast notification system | Medium | 1h |
| Add loading states for mutation buttons | Low | 1h |
| Add mobile sidebar collapse | Medium | 1h |
| Add light/dark mode toggle | Low | 30m |
| Add `NEXT_PUBLIC_API_URL` env documentation | Medium | 15m |

**Outcome:** Resilient, polished UI.

### RC-5: CI/CD & Final Validation

**Goal:** Automated quality gates.

| Task | Priority | Effort |
|------|----------|--------|
| Create GitHub Actions CI workflow | Medium | 1.5h |
| Backend: lint + typecheck + test in CI | Medium | 30m |
| Frontend: build + typecheck in CI | Medium | 30m |
| End-to-end smoke test (scan → view analytics) | Medium | 2h |

**Outcome:** Merge confidence via automation.

---

## 4. Effort Summary

| RC Phase | Tasks | Total Effort | Cumulative |
|----------|-------|-------------|-----------|
| RC-1: Core Workflow | 6 | ~10h | 10h |
| RC-2: Docker & Docs | 5 | ~5h | 15h |
| RC-3: Data Integration | 7 | ~8h | 23h |
| RC-4: Polish | 6 | ~5h | 28h |
| RC-5: CI/CD | 4 | ~4.5h | 32.5h |

**Total to Release Candidate: ~32 hours of focused work**

---

## 5. Recommendation

Ship RC-1 + RC-2 first (15h) — this creates a fully functional, deployable product. RC-3 through RC-5 are quality-of-life improvements that can ship incrementally.

The minimum viable release requires:
1. Users can trigger and monitor a scan
2. Docker deployment works in one command
3. A README explains how to get started

Everything else enhances but doesn't block the core value proposition.
