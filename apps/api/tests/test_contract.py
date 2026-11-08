from scripts.export_openapi import OUT, render


def test_implemented_openapi_is_current() -> None:
    assert OUT.read_text() == render(), "run scripts/export_openapi.py and commit the result"
