from github_app.return_to import (
    build_state,
    is_allowed_return_to,
    origin_of,
    parse_state,
)


def test_build_and_parse_roundtrip_with_return_to():
    state = build_state(
        "nonce-abc", "https://ulam.io/software-services/free-code-audit?x=1"
    )
    nonce, return_to = parse_state(state)
    assert nonce == "nonce-abc"
    assert return_to == "https://ulam.io/software-services/free-code-audit?x=1"


def test_build_and_parse_without_return_to():
    state = build_state("nonce-only", None)
    nonce, return_to = parse_state(state)
    assert nonce == "nonce-only"
    assert return_to is None


def test_parse_state_tolerates_garbage_input():
    # Not valid base64-JSON — should fall back to (raw_input, None) without raising.
    _nonce, return_to = parse_state("@@@not-base64@@@")
    assert return_to is None


def test_origin_of():
    assert origin_of("https://ulam.io/path?q=1") == "https://ulam.io"
    assert origin_of("http://localhost:3000/x") == "http://localhost:3000"
    assert origin_of("/relative") is None
    assert origin_of("javascript:alert(1)") is None


def test_is_allowed_return_to():
    allowed = ["https://ulam.io", "http://localhost:3000"]
    assert is_allowed_return_to("https://ulam.io/anything", allowed) is True
    assert is_allowed_return_to("https://ulam.io.evil.com/x", allowed) is False
    assert is_allowed_return_to("https://evil.com", allowed) is False
    assert is_allowed_return_to(None, allowed) is False
    assert is_allowed_return_to("https://ulam.io", []) is False
