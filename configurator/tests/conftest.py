"""Fixtures shared by every configurator test.

Tests must never write into the operator's own ``%LOCALAPPDATA%\\DuoInput``;
see user_files_guard.py and test_user_files_isolation.py.
"""

from __future__ import annotations

from user_files_guard import _isolated_localappdata, isolated_user_files  # noqa: F401
