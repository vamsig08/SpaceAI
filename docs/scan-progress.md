# Scan Progress Reporting Architecture

**Reference:** ADR-004 (Server-Sent Events), ADR-005 (Multi-Pass Scanning)  
**Date:** 2026-06-23

---

## 1. Overview

Real-time progress reporting uses Server-Sent Events (SSE) to push scan status from backend to frontend. The system supports multiple concurrent subscribers, automatic reconnection, and progress persistence for interrupted connections.

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     Frontend                              │
│                                                           │
│  ┌──────────────────────────────────────────────────┐   │
│  │             useScanProgress() Hook                │   │
│  │                                                    │   │
│  │  const eventSource = new EventSource(             │   │
│  │    `/api/v1/scans/${scanId}/progress`             │   │
│  │  );                                               │   │
│  │                                                    │   │
│  │  eventSource.addEventListener('progress', ...)    │   │
│  │  eventSource.addEventListener('completed', ...)   │   │
│  │  eventSource.addEventListener('error', ...)       │   │
│  └──────────────────────────────────────────────────┘   │
└───────────────────────────┬──────────────────────────────┘
                            │ HTTP GET (text/event-stream)
                            ▼
┌──────────────────────────────────────────────────────────┐
│                     Backend                               │
│                                                           │
│  ┌──────────────────────────────────────────────────┐   │
│  │           SSE Endpoint (API Layer)                 │   │
│  │                                                    │   │
│  │  @router.get("/scans/{scan_id}/progress")         │   │
│  │  async def scan_progress(...):                    │   │
│  │      return StreamingResponse(                    │   │
│  │          event_generator(scan_id),                │   │
│  │          media_type="text/event-stream"           │   │
│  │      )                                            │   │
│  └───────────────────────────┬──────────────────────┘   │
│                              │                            │
│  ┌───────────────────────────▼──────────────────────┐   │
│  │          ProgressReporter (Core)                   │   │
│  │                                                    │   │
│  │  Subscribers: dict[task_id, list[Queue]]          │   │
│  │                                                    │   │
│  │  emit(task_id, event) ──▶ fan-out to all queues   │   │
│  └───────────────────────────▲──────────────────────┘   │
│                              │                            │
│  ┌───────────────────────────┴──────────────────────┐   │
│  │           Scanner Worker (Background)              │   │
│  │                                                    │   │
│  │  Every 1000 files OR every 1 second:              │   │
│  │    reporter.emit(scan_id, ProgressEvent(...))     │   │
│  └──────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

---

## 3. Event Protocol

### 3.1 SSE Wire Format

```
id: 1719136800123
event: progress
data: {"files_scanned":45000,"dirs_scanned":1200,"current_directory":"/Users/vamsig/projects","total_bytes_scanned":53687091200,"estimated_total_files":900000,"eta_seconds":120,"files_per_second":556.2,"memory_usage_mb":145}

id: 1719136801456
event: checkpoint
data: {"checkpoint_number":5,"files_so_far":50000,"last_directory":"/Users/vamsig/projects/spaceai"}

id: 1719136900789
event: completed
data: {"scan_id":"uuid","total_files":892341,"total_dirs":45672,"total_bytes":214748364800,"duration_seconds":87,"files_per_second":10257.9}

```

### 3.2 Event Types

| Event | Frequency | Payload |
|-------|-----------|---------|
| `progress` | Every 1s or 1000 files | files_scanned, dirs_scanned, current_directory, total_bytes_scanned, eta_seconds, files_per_second, memory_usage_mb |
| `checkpoint` | Every 10,000 files | checkpoint_number, files_so_far, last_directory |
| `error` | On recoverable error | error_type, path, message (scan continues) |
| `completed` | Once at end | total_files, total_dirs, total_bytes, duration_seconds, files_per_second |
| `failed` | Once on fatal error | error_type, message, files_scanned_so_far |
| `cancelled` | Once on user cancel | files_scanned_so_far, checkpoint_saved |

### 3.3 Event ID Strategy

Each event carries a monotonically increasing `id` (Unix timestamp in milliseconds). This enables:
- Browser `EventSource` auto-reconnection with `Last-Event-ID` header.
- Server can replay missed events from a bounded buffer.
- Frontend knows the last event it received for reconnection.

---

## 4. Backend Implementation

### 4.1 ProgressReporter

```python
class ProgressEvent:
    event_type: str          # progress | checkpoint | error | completed | failed | cancelled
    data: dict               # Event-specific payload
    event_id: int            # Monotonic timestamp (ms)

class ProgressReporter:
    """Fan-out progress events to SSE subscribers with bounded buffer."""
    
    def __init__(self, buffer_size: int = 100):
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._buffers: dict[str, deque] = defaultdict(lambda: deque(maxlen=buffer_size))
        self._lock: asyncio.Lock = asyncio.Lock()
    
    async def subscribe(self, task_id: str, last_event_id: int | None = None) -> asyncio.Queue:
        """Create subscriber. Replays buffered events if last_event_id provided."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        
        async with self._lock:
            # Replay missed events on reconnection
            if last_event_id is not None:
                for event in self._buffers[task_id]:
                    if event.event_id > last_event_id:
                        await queue.put(event)
            
            self._subscribers[task_id].append(queue)
        
        return queue
    
    async def unsubscribe(self, task_id: str, queue: asyncio.Queue) -> None:
        """Remove subscriber queue."""
        async with self._lock:
            self._subscribers[task_id].remove(queue)
            if not self._subscribers[task_id]:
                del self._subscribers[task_id]
    
    async def emit(self, task_id: str, event: ProgressEvent) -> None:
        """Push event to all subscribers and buffer."""
        async with self._lock:
            self._buffers[task_id].append(event)
            
            dead_queues = []
            for queue in self._subscribers[task_id]:
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    dead_queues.append(queue)  # Slow consumer, drop
            
            for q in dead_queues:
                self._subscribers[task_id].remove(q)
```

### 4.2 SSE Endpoint

```python
@router.get("/scans/{scan_id}/progress")
async def scan_progress(
    scan_id: str,
    request: Request,
    last_event_id: int | None = Header(None, alias="Last-Event-ID"),
    reporter: ProgressReporter = Depends(get_progress_reporter),
    scan_service: ScanService = Depends(get_scan_service),
) -> StreamingResponse:
    """Stream scan progress via SSE."""
    
    # Verify scan exists
    scan = await scan_service.get_scan(scan_id)
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")
    
    # If scan already completed, return final state immediately
    if scan.status in ("completed", "failed", "cancelled"):
        return StreamingResponse(
            terminal_event_generator(scan),
            media_type="text/event-stream",
        )
    
    # Subscribe to live progress
    queue = await reporter.subscribe(scan_id, last_event_id)
    
    async def event_generator():
        try:
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break
                
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield format_sse(event)
                    
                    # Terminal events close the stream
                    if event.event_type in ("completed", "failed", "cancelled"):
                        break
                        
                except asyncio.TimeoutError:
                    # Send keepalive comment to prevent proxy timeouts
                    yield ": keepalive\n\n"
        finally:
            await reporter.unsubscribe(scan_id, queue)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
```

### 4.3 SSE Formatting

```python
def format_sse(event: ProgressEvent) -> str:
    """Format a ProgressEvent as an SSE wire format string."""
    lines = []
    lines.append(f"id: {event.event_id}")
    lines.append(f"event: {event.event_type}")
    lines.append(f"data: {json.dumps(event.data)}")
    lines.append("")  # Empty line terminates event
    lines.append("")
    return "\n".join(lines)
```

---

## 5. Frontend Implementation

### 5.1 useScanProgress Hook

```typescript
interface ScanProgress {
  filesScanned: number;
  dirsScanned: number;
  currentDirectory: string;
  totalBytesScanned: number;
  etaSeconds: number | null;
  filesPerSecond: number;
  memoryUsageMb: number;
}

interface UseScanProgressReturn {
  progress: ScanProgress | null;
  status: 'connecting' | 'streaming' | 'completed' | 'failed' | 'cancelled' | 'disconnected';
  error: string | null;
}

function useScanProgress(scanId: string | null): UseScanProgressReturn {
  // Uses EventSource API
  // Handles reconnection automatically (browser built-in)
  // Parses JSON data from SSE events
  // Updates React state on each progress event
  // Cleans up on unmount or scanId change
}
```

### 5.2 Progress Display

```
┌─────────────────────────────────────────────────────┐
│  Scanning: /Users/vamsig/projects/spaceai            │
│                                                       │
│  ████████████████░░░░░░░░░░░░░░  52%                │
│                                                       │
│  Files: 456,230 / ~900,000     Dirs: 12,450         │
│  Speed: 10,234 files/sec       Size: 125.4 GB       │
│  ETA: ~45 seconds              Memory: 145 MB       │
│                                                       │
│  [Cancel Scan]                                       │
└─────────────────────────────────────────────────────┘
```

ETA calculation:
- Based on: `remaining_estimate = (estimated_total - files_scanned) / files_per_second`
- `estimated_total` refined as scan progresses (based on directory density sampling).
- Display "Calculating..." for first 5 seconds while rate stabilizes.

---

## 6. Edge Cases

### 6.1 Client Reconnection

1. Browser `EventSource` automatically reconnects on connection drop.
2. Sends `Last-Event-ID` header with the last received event ID.
3. Server replays missed events from buffer (last 100 events).
4. If buffer doesn't contain the event, send current state snapshot instead.

### 6.2 No Active Subscribers

If no clients are listening, events are still emitted to the buffer (cost: negligible memory). When a client connects mid-scan, they receive the current progress state immediately.

### 6.3 Scan Completes Before Client Connects

If a client requests `/progress` for a completed scan:
- Return a single `completed` event with final stats.
- Close the stream immediately.

### 6.4 Proxy/Timeout Handling

- Keepalive comments (`: keepalive\n\n`) sent every 30 seconds.
- `X-Accel-Buffering: no` header for nginx.
- `Cache-Control: no-cache` prevents CDN caching.
- Frontend `EventSource` has built-in reconnection with exponential backoff.

---

## 7. Progress Emission from Scanner

The scanner worker emits progress using two strategies:

### 7.1 Count-Based (Primary)

```python
# Every 1000 files processed
if files_in_batch >= 1000:
    await reporter.emit(scan_id, ProgressEvent(
        event_type="progress",
        data=build_progress_data(task_state),
        event_id=int(time.time() * 1000),
    ))
    files_in_batch = 0
```

### 7.2 Time-Based (Fallback for Slow Directories)

```python
# Every 1 second regardless of file count
if time.time() - last_emit_time >= 1.0:
    await reporter.emit(scan_id, ProgressEvent(
        event_type="progress",
        data=build_progress_data(task_state),
        event_id=int(time.time() * 1000),
    ))
    last_emit_time = time.time()
```

This ensures the UI updates at least once per second even when traversing directories with very large files (where individual file processing is slow).

---

## 8. Polling Fallback API

For environments where SSE is unavailable (some corporate proxies strip event-stream), provide a polling endpoint:

```
GET /api/v1/scans/{scan_id}/status
```

Response:
```json
{
  "data": {
    "scan_id": "uuid",
    "status": "running",
    "progress": {
      "files_scanned": 456230,
      "dirs_scanned": 12450,
      "total_bytes_scanned": 134652825600,
      "files_per_second": 10234.5,
      "eta_seconds": 45,
      "current_directory": "/Users/vamsig/projects"
    },
    "started_at": "2026-06-23T10:00:00Z"
  }
}
```

Frontend falls back to polling (2-second interval) if EventSource connection fails 3 times.
