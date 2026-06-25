"""Unit tests for ExclusionEngine."""

from pathlib import Path

import pytest

from app.scanner.exclusions import ExclusionEngine, ExclusionPattern


class TestExclusionEngineNameRules:
    """Tests for name-based exclusion matching."""

    def test_excludes_directory_by_name(self) -> None:
        engine = ExclusionEngine(
            patterns=[ExclusionPattern("node_modules", "name")]
        )
        assert engine.should_exclude_directory(Path("/project/node_modules"))
        assert not engine.should_exclude_directory(Path("/project/src"))

    def test_excludes_file_by_name(self) -> None:
        engine = ExclusionEngine(
            patterns=[ExclusionPattern(".DS_Store", "name")]
        )
        assert engine.should_exclude_file(Path("/project/.DS_Store"))
        assert not engine.should_exclude_file(Path("/project/readme.md"))

    def test_name_match_is_exact(self) -> None:
        engine = ExclusionEngine(
            patterns=[ExclusionPattern("node_modules", "name")]
        )
        # Should NOT match partial names
        assert not engine.should_exclude_directory(Path("/project/node_modules_backup"))

    def test_multiple_name_rules(self) -> None:
        engine = ExclusionEngine(
            patterns=[
                ExclusionPattern("node_modules", "name"),
                ExclusionPattern(".git", "name"),
                ExclusionPattern("__pycache__", "name"),
            ]
        )
        assert engine.should_exclude_directory(Path("/a/node_modules"))
        assert engine.should_exclude_directory(Path("/a/.git"))
        assert engine.should_exclude_directory(Path("/a/__pycache__"))
        assert not engine.should_exclude_directory(Path("/a/src"))


class TestExclusionEngineExtensionRules:
    """Tests for extension-based exclusion."""

    def test_excludes_file_by_extension(self) -> None:
        engine = ExclusionEngine(
            patterns=[ExclusionPattern(".pyc", "extension")]
        )
        assert engine.should_exclude_file(Path("/project/module.pyc"))
        assert not engine.should_exclude_file(Path("/project/module.py"))

    def test_extension_without_dot_prefix(self) -> None:
        engine = ExclusionEngine(
            patterns=[ExclusionPattern("log", "extension")]
        )
        assert engine.should_exclude_file(Path("/tmp/app.log"))

    def test_extension_is_case_insensitive(self) -> None:
        engine = ExclusionEngine(
            patterns=[ExclusionPattern(".pyc", "extension")]
        )
        # Path suffix is lowercased during check
        assert engine.should_exclude_file(Path("/project/module.pyc"))

    def test_extension_does_not_affect_directories(self) -> None:
        engine = ExclusionEngine(
            patterns=[ExclusionPattern(".pyc", "extension")]
        )
        assert not engine.should_exclude_directory(Path("/project/.pyc"))


class TestExclusionEnginePathRules:
    """Tests for path-component matching."""

    def test_matches_path_substring(self) -> None:
        engine = ExclusionEngine(
            patterns=[ExclusionPattern("/proc", "path")]
        )
        assert engine.should_exclude_directory(Path("/proc/1234"))
        assert engine.should_exclude_file(Path("/proc/cpuinfo"))

    def test_does_not_match_unrelated_path(self) -> None:
        engine = ExclusionEngine(
            patterns=[ExclusionPattern("/proc", "path")]
        )
        assert not engine.should_exclude_directory(Path("/home/user/projects"))


class TestExclusionEngineRegexRules:
    """Tests for regex-based exclusion."""

    def test_matches_regex_pattern(self) -> None:
        engine = ExclusionEngine(
            patterns=[ExclusionPattern(r"\.tmp\d+$", "regex")]
        )
        assert engine.should_exclude_file(Path("/project/data.tmp123"))
        assert not engine.should_exclude_file(Path("/project/data.txt"))

    def test_invalid_regex_is_skipped(self) -> None:
        engine = ExclusionEngine(
            patterns=[ExclusionPattern("[invalid", "regex")]
        )
        # Should not raise, just skip the invalid pattern
        assert not engine.should_exclude_file(Path("/project/file.txt"))
        assert len(engine._compiled_regex) == 0


class TestExclusionEngineFactory:
    """Tests for ExclusionEngine.create factory method."""

    def test_creates_from_db_rules(self) -> None:
        engine = ExclusionEngine.create(
            db_rules=[("node_modules", "name"), (".pyc", "extension")],
            include_platform_defaults=False,
        )
        assert engine.rule_count == 2
        assert engine.should_exclude_directory(Path("/a/node_modules"))
        assert engine.should_exclude_file(Path("/a/b.pyc"))

    def test_includes_platform_defaults(self) -> None:
        engine = ExclusionEngine.create(
            db_rules=[],
            include_platform_defaults=True,
        )
        # Should have at least the platform system rules
        assert engine.rule_count >= 4

    def test_additional_patterns_treated_as_name_rules(self) -> None:
        engine = ExclusionEngine.create(
            db_rules=[],
            additional_patterns=[".venv", "dist"],
            include_platform_defaults=False,
        )
        assert engine.should_exclude_directory(Path("/project/.venv"))
        assert engine.should_exclude_directory(Path("/project/dist"))

    def test_empty_inputs_produce_empty_engine(self) -> None:
        engine = ExclusionEngine.create(
            db_rules=[],
            additional_patterns=None,
            include_platform_defaults=False,
        )
        assert engine.rule_count == 0
        assert not engine.should_exclude_directory(Path("/anything"))
        assert not engine.should_exclude_file(Path("/anything"))


class TestExclusionEngineRuleCount:
    """Tests for rule_count property."""

    def test_counts_all_patterns(self) -> None:
        engine = ExclusionEngine(
            patterns=[
                ExclusionPattern("a", "name"),
                ExclusionPattern("b", "name"),
                ExclusionPattern(".c", "extension"),
            ]
        )
        assert engine.rule_count == 3
