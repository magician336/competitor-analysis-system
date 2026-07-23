from __future__ import annotations

from pathlib import Path

import pytest

import shell_runner


def test_normal_value_uses_argument_list_without_shell(monkeypatch) -> None:
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return object()

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(
        "os.system",
        lambda *_: (_ for _ in ()).throw(AssertionError("os.system is forbidden")),
    )
    shell_runner.list_file("report.txt")
    assert calls, "secure implementation must invoke subprocess.run"
    args, kwargs = calls[0]
    assert isinstance(args, list)
    assert args[-1] == "report.txt"
    assert kwargs.get("shell", False) is False
    assert kwargs.get("check") is True


@pytest.mark.parametrize(
    "payload",
    ["report.txt; touch pwned", "$(whoami)", "a && whoami", "../secret"],
)
def test_attack_payload_is_rejected(payload: str, monkeypatch) -> None:
    monkeypatch.setattr(
        "os.system",
        lambda *_: (_ for _ in ()).throw(AssertionError("payload reached a shell")),
    )
    monkeypatch.setattr(
        "subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid payload reached subprocess")
        ),
    )
    with pytest.raises(ValueError):
        shell_runner.list_file(payload)


def test_security_report_records_cwe_and_mitigation() -> None:
    report = Path("SECURITY_REPORT.md").read_text(encoding="utf-8").casefold()
    assert "cwe-78" in report
    assert "subprocess" in report
    assert "allowlist" in report or "白名单" in report
