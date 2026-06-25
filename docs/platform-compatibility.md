# Platform Compatibility — Filesystem Operations

**Reference:** ADR-006 (Cross-Platform File System Abstraction)  
**Date:** 2026-06-23

---

## 1. Path Handling Strategy

### 1.1 Windows Paths

| Scenario | Example | Handling |
|----------|---------|----------|
| Standard path | `C:\Users\dev\projects` | Convert to forward slash in DB: `C:/Users/dev/projects` |
| UNC path (network) | `\\server\share\folder` | Store as `//server/share/folder`; prefix with `\\?\UNC\` for API calls |
| Long path (>260 chars) | `C:\very\long\...` | Use `\\?\` extended path prefix via `os.fsdecode` |
| Drive-relative | `D:folder\file.txt` | Resolve to absolute via `Path.resolve()` before storing |
| Path with spaces | `C:\Program Files\app` | Handled natively by `pathlib.Path`; no quoting needed |

**Normalization function:**
```python
def normalize_path_for_storage(path: Path) -> str:
    """Normalize any platform path to a consistent forward-slash string."""
    resolved = path.resolve()  # Resolves symlinks, relative parts, drive-relative
    return str(resolved).replace("\\", "/")
```

**Restoration function:**
```python
def storage_path_to_native(stored_path: str) -> Path:
    """Convert stored path back to platform-native Path object."""
    if sys.platform == "win32":
        # Convert forward slashes back for Windows API calls
        native = stored_path.replace("/", "\\")
        # Add long path prefix if needed
        if len(native) > 250 and not native.startswith("\\\\?\\"):
            native = "\\\\?\\" + native
        return Path(native)
    return Path(stored_path)
```

### 1.2 macOS Paths

| Scenario | Example | Handling |
|----------|---------|----------|
| Standard POSIX | `/Users/dev/projects` | Store as-is |
| Unicode (NFD) | `/Users/dev/café` | macOS uses NFD normalization; normalize to NFC via `unicodedata.normalize('NFC', ...)` |
| Case-insensitive FS | `File.TXT` vs `file.txt` | APFS is case-insensitive by default; store as-found, compare case-insensitively for duplicates |
| Resource forks | `._filename` | Exclude by default (system artifact) |
| .DS_Store | `.DS_Store` | Exclude by default |

### 1.3 Linux Paths

| Scenario | Example | Handling |
|----------|---------|----------|
| Standard POSIX | `/home/dev/projects` | Store as-is |
| Case-sensitive | `File.txt` vs `file.txt` | These are different files; store exactly as found |
| Bytes in filenames | Non-UTF8 filenames | Use `os.fsdecode()` with surrogatepass; log warning if non-UTF8 |
| Mount points | `/mnt/external` | Scan across mount boundaries by default; configurable exclusion |
| /proc, /sys, /dev | Virtual filesystems | Excluded by default (system exclusion rules) |

---

## 2. Symbolic Links

### 2.1 Strategy: Follow with Cycle Detection

```python
class SymlinkPolicy:
    """Controls how the scanner handles symbolic links."""
    
    FOLLOW = "follow"          # Follow symlinks, detect cycles
    SKIP = "skip"              # Skip all symlinks
    RECORD_ONLY = "record"     # Record symlink target without following
```

Default: `FOLLOW` with cycle detection.

### 2.2 Cycle Detection Algorithm

```python
class CycleDetector:
    """Detects symlink cycles using inode + device tracking."""
    
    def __init__(self):
        self._visited_dirs: set[tuple[int, int]] = set()  # (device, inode) pairs
    
    def check_and_record(self, path: Path) -> bool:
        """Returns True if this directory was already visited (cycle detected)."""
        stat = path.stat()  # follows symlink
        key = (stat.st_dev, stat.st_ino)
        if key in self._visited_dirs:
            return True  # CYCLE - do not enter
        self._visited_dirs.add(key)
        return False
```

### 2.3 Platform-Specific Symlink Behavior

| Platform | Symlink Type | Detection | Follow |
|----------|-------------|-----------|--------|
| macOS | POSIX symlink | `Path.is_symlink()` | Yes, with cycle detection |
| Linux | POSIX symlink | `Path.is_symlink()` | Yes, with cycle detection |
| Windows | Symlink (NTFS) | `Path.is_symlink()` | Yes, with cycle detection |
| Windows | Junction point | `os.path.isdir()` + check reparse point | Yes, with cycle detection |
| Windows | Hardlink | Same inode detection | Automatically handled by inode tracking |

### 2.4 Broken Symlinks

```python
def handle_entry(entry: os.DirEntry) -> FileInfo | None:
    try:
        if entry.is_symlink():
            # Check if target exists
            target = Path(entry.path).resolve()
            if not target.exists():
                logger.warning("broken_symlink", path=entry.path, target=str(target))
                return None  # Skip broken symlinks
        # Continue with normal processing...
    except OSError as e:
        logger.warning("symlink_error", path=entry.path, error=str(e))
        return None
```

---

## 3. Junction Points (Windows)

### 3.1 What Are They

Junction points are Windows-specific directory links (similar to symlinks but older):
- Created by `mklink /J target link`
- Only work for directories
- Cannot span network paths
- Resolved transparently by the Windows filesystem

### 3.2 Detection and Handling

```python
import ctypes

def is_junction(path: Path) -> bool:
    """Detect Windows junction points."""
    if sys.platform != "win32":
        return False
    
    FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
    IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
    
    attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
    if attrs == -1:
        return False
    return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)
```

**Strategy**: Treat junction points identically to symlinks — follow with cycle detection.

---

## 4. Permission Errors

### 4.1 Handling Strategy: Log and Skip

Permission errors are non-fatal. The scanner continues with accessible files.

```python
async def scan_directory(directory: Path, task_state: TaskState) -> list[FileInfo]:
    results = []
    try:
        entries = list(os.scandir(directory))
    except PermissionError:
        logger.warning("permission_denied_directory", 
                       path=str(directory),
                       scan_id=task_state.scan_id)
        task_state.progress.errors_skipped += 1
        return results
    
    for entry in entries:
        try:
            info = collect_file_info(entry)
            results.append(info)
        except PermissionError:
            logger.warning("permission_denied_file",
                           path=entry.path,
                           scan_id=task_state.scan_id)
            task_state.progress.errors_skipped += 1
        except OSError as e:
            logger.warning("os_error_file",
                           path=entry.path,
                           error=str(e),
                           scan_id=task_state.scan_id)
            task_state.progress.errors_skipped += 1
    
    return results
```

### 4.2 Platform-Specific Permission Issues

| Platform | Common Causes | Handling |
|----------|--------------|----------|
| macOS | SIP-protected dirs (`/System`), TCC restrictions | Skip; already in default exclusions |
| Linux | Root-owned files, AppArmor/SELinux | Skip; log warning |
| Windows | System files, NTFS ACLs, UAC | Skip; no attempt to elevate |

### 4.3 Error Reporting

After scan completion, report:
```json
{
  "scan_id": "uuid",
  "total_files": 892341,
  "errors_skipped": 23,
  "error_breakdown": {
    "permission_denied": 18,
    "broken_symlink": 3,
    "os_error": 2
  }
}
```

---

## 5. Network Drives

### 5.1 Detection

```python
def is_network_path(path: Path) -> bool:
    """Detect if path is on a network drive."""
    if sys.platform == "win32":
        # UNC path: \\server\share
        return str(path).startswith("\\\\") or str(path).startswith("//")
    else:
        # Check if mount point is network filesystem
        # Linux: check /proc/mounts for nfs, cifs, smbfs
        # macOS: check mount output for smbfs, nfs, afpfs
        return _check_mount_type(path) in ("nfs", "cifs", "smbfs", "afpfs")

def _check_mount_type(path: Path) -> str | None:
    """Check filesystem type of the mount containing path."""
    if sys.platform == "linux":
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                mount_point = parts[1]
                fs_type = parts[2]
                if str(path).startswith(mount_point):
                    return fs_type
    elif sys.platform == "darwin":
        # Use os.statvfs or parse mount output
        ...
    return None
```

### 5.2 Network Drive Scanning Policy

| Policy | Behavior |
|--------|----------|
| **Default** | Scan network paths if explicitly provided as root_path |
| **Exclusion** | Do not follow symlinks/mounts INTO network paths during local scans |
| **Performance** | Warn user that network scans are significantly slower |
| **Timeout** | Per-file timeout of 5 seconds for stat calls on network paths |
| **Resilience** | Handle network disconnection gracefully (checkpoint + resume) |

### 5.3 Performance Considerations

Network drives have high-latency I/O:
- NFS: 1-10ms per stat call (vs <0.1ms local SSD)
- SMB: 5-50ms per stat call
- Impact: 1M files at 10ms/stat = 10,000 seconds = ~2.7 hours

**Mitigation**: 
- Increase thread pool to 8-16 for network scans (configurable).
- Emit warning in SSE stream: "Network drive detected. Scan may be significantly slower."
- Do NOT count network scans against the 30-minute NFR (document as "local SSD" target).

---

## 6. External Drives

### 6.1 Detection

```python
def is_external_drive(path: Path) -> bool:
    """Detect if path is on an external/removable drive."""
    if sys.platform == "win32":
        import ctypes
        drive = str(path.resolve())[:3]  # e.g., "D:\\"
        DRIVE_REMOVABLE = 2
        drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive)
        return drive_type == DRIVE_REMOVABLE
    elif sys.platform == "darwin":
        return str(path).startswith("/Volumes/") and not str(path).startswith("/Volumes/Macintosh HD")
    else:
        # Linux: check if mount is in /media/ or /mnt/
        return str(path).startswith(("/media/", "/mnt/", "/run/media/"))
```

### 6.2 External Drive Policies

| Concern | Strategy |
|---------|----------|
| Drive disconnection mid-scan | Checkpoint covers this; scan fails gracefully with resume capability |
| Slow USB2 drives | Same approach as network: warn user, increase timeout |
| Drive letter changes (Windows) | Store path with drive letter; if not found on resume, prompt user |
| macOS volume names change | Store full `/Volumes/DriveName/...` path |
| Permissions on external FAT32 | FAT32 has no permissions; owner/permissions stored as NULL |

---

## 7. Special Filesystem Entries

### 7.1 Skip List (Always Excluded)

| Entry | Platform | Reason |
|-------|----------|--------|
| `/proc` | Linux | Virtual; infinite recursion risk |
| `/sys` | Linux | Virtual; no real files |
| `/dev` | Linux | Device files; not scannable |
| `/run` | Linux | Runtime data; transient |
| `.Spotlight-V100` | macOS | Spotlight index; system-managed |
| `.fseventsd` | macOS | FSEvents log; system-managed |
| `$Recycle.Bin` | Windows | Recycle bin; managed by OS |
| `System Volume Information` | Windows | System restore points |
| `pagefile.sys` | Windows | Paging file; locked |
| `hiberfil.sys` | Windows | Hibernation file; locked |

### 7.2 Named Pipes, Device Files, Sockets

```python
def should_process_entry(entry: os.DirEntry) -> bool:
    """Only process regular files and directories."""
    try:
        if entry.is_file(follow_symlinks=True):
            return True
        if entry.is_dir(follow_symlinks=True):
            return True
        # Skip: named pipes, sockets, device files, block devices
        return False
    except OSError:
        return False
```

---

## 8. Unicode and Encoding

### 8.1 Strategy

- All paths stored as UTF-8 strings in SQLite.
- macOS NFD → NFC normalization applied before storage.
- Windows: paths are natively UTF-16; Python handles conversion transparently.
- Linux: filenames may be arbitrary bytes. Use `os.fsdecode()` with `surrogatepass` error handler.

### 8.2 Non-UTF8 Filenames (Linux)

```python
def safe_path_string(path: os.PathLike) -> str:
    """Convert any path to a safe UTF-8 string, handling non-UTF8 bytes."""
    try:
        return str(path)
    except UnicodeDecodeError:
        # Use surrogateescape for bytes that aren't valid UTF-8
        raw = os.fsencode(path)
        return raw.decode("utf-8", errors="surrogateescape")
```

These files are stored with surrogate escapes and flagged in the UI as "filename contains non-standard characters."

---

## 9. File Locking

### 9.1 Read-Only Operations (Scan, Hash)

The scanner only reads files. File locks don't prevent reading on most platforms:
- **Windows**: Exclusive locks (`LOCK_EX`) prevent reading. Handle with `PermissionError` catch.
- **macOS/Linux**: Advisory locks only; reading is always allowed.

### 9.2 Cleanup Operations (Trash/Delete)

For cleanup, locked files cannot be moved/deleted:

```python
async def safe_move_to_trash(path: Path) -> MoveResult:
    """Attempt to move a file to trash, handling locks gracefully."""
    try:
        send2trash.send2trash(str(path))
        return MoveResult(success=True)
    except PermissionError:
        return MoveResult(success=False, error="file_locked", 
                          message=f"Cannot move {path}: file is in use")
    except OSError as e:
        return MoveResult(success=False, error="os_error", message=str(e))
```

Batch cleanup behavior: If any file in a batch is locked, skip it (do not roll back the entire batch for a single lock). Report partial success.

---

## 10. Docker Container Considerations

### 10.1 Volume Mounts

When running SpaceAI in Docker, the host filesystem is accessed via volume mounts:
```yaml
volumes:
  - /host/path:/scan:ro  # Read-only mount for scanning
  - ./data:/app/data      # Database persistence
```

Implications:
- Paths in DB are container paths, not host paths. Document this mapping.
- `send2trash` will not work (no desktop environment). Use SpaceAI-managed trash.
- Permissions reflect the container user, not the host user.
- Symlinks that point outside the mounted volume will be broken.

### 10.2 Headless Trash Fallback

```python
def get_trash_handler() -> TrashHandler:
    """Select appropriate trash mechanism."""
    if _is_docker_environment():
        return ManagedTrashHandler(base_dir=Path("/app/data/trash"))
    else:
        return SystemTrashHandler()  # Uses send2trash

def _is_docker_environment() -> bool:
    """Detect if running inside Docker."""
    return (
        Path("/.dockerenv").exists() or
        os.environ.get("SPACEAI_DOCKER") == "1"
    )
```
