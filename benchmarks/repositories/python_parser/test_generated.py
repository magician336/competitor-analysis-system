from parser import parse_kv_line


def test_single_pair() -> None:
    """Starter test: deliberately covers only the simplest happy path."""

    assert parse_kv_line("language=Python") == {"language": "Python"}

