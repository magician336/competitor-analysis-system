from __future__ import annotations

import os
from pathlib import Path

import pytest

from download import DownloadError, read_download


def test_legal_file_is_returned(tmp_path: Path) -> None:
    root = tmp_path / "downloads"
    root.mkdir()
    (root / "notes.txt").write_text("safe", encoding="utf-8")
    assert read_download(root, "notes.txt") == b"safe"


@pytest.mark.parametrize("requested", ["../secret.txt", "..\\secret.txt", "/etc/passwd"])
def test_traversal_and_absolute_paths_are_rejected(tmp_path: Path, requested: str) -> None:
    root = tmp_path / "downloads"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    with pytest.raises(DownloadError) as captured:
        read_download(root, requested)
    message = str(captured.value)
    assert str(tmp_path) not in message
    assert "secret" not in message.casefold()


def test_symlink_escape_is_rejected_when_supported(tmp_path: Path) -> None:
    root = tmp_path / "downloads"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = root / "link.txt"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    with pytest.raises(DownloadError):
        read_download(root, "link.txt")


def test_missing_file_uses_generic_error(tmp_path: Path) -> None:
    root = tmp_path / "downloads"
    root.mkdir()
    with pytest.raises(DownloadError) as captured:
        read_download(root, "missing.txt")
    assert str(root) not in str(captured.value)


def test_report_covers_required_risks() -> None:
    report = Path("SECURITY_REPORT.md").read_text(encoding="utf-8").casefold()
    assert "cwe-22" in report
    assert "symlink" in report or "符号链接" in report
    assert "generic" in report or "统一" in report
