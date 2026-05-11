"""Compatibility wrapper for indexer utilities."""
from cortex.config.settings import load_settings
from cortex.config.tuning import (
    HARDWARE_PROFILES,
    PRESETS,
    DEFAULTS,
    detect_hardware_profile,
    get_tuning_params,
    _log_tuning_report,
)
from cortex.scanner.ignores import DEFAULT_IGNORES, load_gitignore, should_ignore
from cortex.scanner.filters import should_include, get_module_name
from cortex.scanner.finder import get_index_roots, _iter_index_root_files, scan_files
from cortex.utils.text import strip_frontmatter, compute_hash

__all__ = [
    "load_settings",
    "HARDWARE_PROFILES",
    "PRESETS",
    "DEFAULTS",
    "detect_hardware_profile",
    "get_tuning_params",
    "_log_tuning_report",
    "DEFAULT_IGNORES",
    "load_gitignore",
    "should_ignore",
    "should_include",
    "get_module_name",
    "get_index_roots",
    "_iter_index_root_files",
    "scan_files",
    "strip_frontmatter",
    "compute_hash",
]
