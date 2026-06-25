"""Exclusion rules engine for filtering paths during filesystem scanning.

Supports multiple rule types:
- name: Match against the basename of a file or directory
- extension: Match against file extension (e.g., ".pyc")
- path: Match against any path component
- regex: Match against the full path using regex patterns
"""

from __future__ import annotations

import fnmatch
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ExclusionPattern:
    """A single exclusion rule with its type and compiled matcher."""

    pattern: str
    rule_type: str  # name | extension | path | regex
    description: str = ""
    is_system: bool = False


# Platform-specific system directories that should always be excluded
_SYSTEM_EXCLUSIONS_MACOS: list[ExclusionPattern] = [
    ExclusionPattern("/System", "path", "macOS system directory", True),
    ExclusionPattern("/Library", "path", "macOS global library", True),
    ExclusionPattern("Library", "name", "macOS user library", True),
    ExclusionPattern(".Spotlight-V100", "name", "Spotlight index", True),
    ExclusionPattern(".fseventsd", "name", "FSEvents log", True),
    ExclusionPattern(".Trashes", "name", "macOS volume trash", True),
    ExclusionPattern(".Trash", "name", "macOS user trash", True),
    ExclusionPattern(".DS_Store", "name", "Finder metadata", True),
]

_SYSTEM_EXCLUSIONS_LINUX: list[ExclusionPattern] = [
    ExclusionPattern("/proc", "path", "Proc filesystem", True),
    ExclusionPattern("/sys", "path", "Sysfs filesystem", True),
    ExclusionPattern("/dev", "path", "Device files", True),
    ExclusionPattern("/run", "path", "Runtime data", True),
    ExclusionPattern("/snap", "path", "Snap packages", True),
]

_SYSTEM_EXCLUSIONS_WINDOWS: list[ExclusionPattern] = [
    ExclusionPattern("$Recycle.Bin", "name", "Windows Recycle Bin", True),
    ExclusionPattern("System Volume Information", "name", "System restore", True),
    ExclusionPattern("pagefile.sys", "name", "Page file", True),
    ExclusionPattern("hiberfil.sys", "name", "Hibernation file", True),
    ExclusionPattern("swapfile.sys", "name", "Swap file", True),
]


def _get_platform_exclusions() -> list[ExclusionPattern]:
    """Get system exclusions appropriate for the current platform."""
    if sys.platform == "darwin":
        return _SYSTEM_EXCLUSIONS_MACOS
    elif sys.platform == "win32":
        return _SYSTEM_EXCLUSIONS_WINDOWS
    else:
        return _SYSTEM_EXCLUSIONS_LINUX


@dataclass
class ExclusionEngine:
    """Evaluates paths against a set of exclusion rules.

    Compiles rules at initialization for fast matching during scan traversal.
    Supports both directory exclusions (skip entire subtrees) and file exclusions.
    """

    patterns: list[ExclusionPattern] = field(default_factory=list)
    _compiled_regex: dict[str, re.Pattern[str]] = field(
        default_factory=dict, init=False, repr=False
    )
    _name_set: set[str] = field(default_factory=set, init=False, repr=False)
    _extension_set: set[str] = field(default_factory=set, init=False, repr=False)
    _path_patterns: list[str] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        """Compile patterns into optimized lookup structures."""
        self._compile()

    def _compile(self) -> None:
        """Build fast lookup sets from patterns."""
        self._name_set.clear()
        self._extension_set.clear()
        self._path_patterns.clear()
        self._compiled_regex.clear()

        for p in self.patterns:
            if p.rule_type == "name":
                self._name_set.add(p.pattern)
            elif p.rule_type == "extension":
                ext = p.pattern if p.pattern.startswith(".") else f".{p.pattern}"
                self._extension_set.add(ext.lower())
            elif p.rule_type == "path":
                self._path_patterns.append(p.pattern)
            elif p.rule_type == "regex":
                try:
                    self._compiled_regex[p.pattern] = re.compile(p.pattern)
                except re.error as e:
                    logger.warning(
                        "invalid_exclusion_regex",
                        pattern=p.pattern,
                        error=str(e),
                    )

    @classmethod
    def create(
        cls,
        db_rules: list[tuple[str, str]],
        additional_patterns: list[str] | None = None,
        include_platform_defaults: bool = True,
    ) -> ExclusionEngine:
        """Factory method to build an ExclusionEngine from various sources.

        Args:
            db_rules: List of (pattern, rule_type) tuples from the database.
            additional_patterns: Extra name-based patterns from the scan request.
            include_platform_defaults: Whether to include OS-specific system exclusions.

        Returns:
            A configured ExclusionEngine ready for path evaluation.
        """
        patterns: list[ExclusionPattern] = []

        # Add platform defaults
        if include_platform_defaults:
            patterns.extend(_get_platform_exclusions())

        # Add database rules
        for pattern, rule_type in db_rules:
            patterns.append(ExclusionPattern(pattern=pattern, rule_type=rule_type))

        # Add request-specific exclusions (treated as name-based)
        if additional_patterns:
            for p in additional_patterns:
                patterns.append(ExclusionPattern(pattern=p, rule_type="name"))

        engine = cls(patterns=patterns)
        logger.debug(
            "exclusion_engine_created",
            total_rules=len(patterns),
            name_rules=len(engine._name_set),
            extension_rules=len(engine._extension_set),
            path_rules=len(engine._path_patterns),
            regex_rules=len(engine._compiled_regex),
        )
        return engine

    def should_exclude_directory(self, dir_path: Path) -> bool:
        """Check if a directory should be excluded (skip entire subtree).

        This is the hot path — called for every directory during traversal.
        Optimized with set lookups before more expensive checks.

        Args:
            dir_path: The directory path to evaluate.

        Returns:
            True if the directory should be skipped.
        """
        name = dir_path.name

        # Fast path: name-based exclusion (O(1) set lookup)
        if name in self._name_set:
            return True

        # Path component matching
        path_str = str(dir_path)
        for pattern in self._path_patterns:
            if pattern in path_str:
                return True

        # Regex matching (slowest, checked last)
        for compiled in self._compiled_regex.values():
            if compiled.search(path_str):
                return True

        return False

    def should_exclude_file(self, file_path: Path) -> bool:
        """Check if a file should be excluded from results.

        Args:
            file_path: The file path to evaluate.

        Returns:
            True if the file should be skipped.
        """
        name = file_path.name

        # Name-based exclusion
        if name in self._name_set:
            return True

        # Extension-based exclusion
        ext = file_path.suffix.lower()
        if ext and ext in self._extension_set:
            return True

        # Path matching
        path_str = str(file_path)
        for pattern in self._path_patterns:
            if pattern in path_str:
                return True

        # Regex matching
        for compiled in self._compiled_regex.values():
            if compiled.search(path_str):
                return True

        return False

    @property
    def rule_count(self) -> int:
        """Total number of active exclusion rules."""
        return len(self.patterns)
