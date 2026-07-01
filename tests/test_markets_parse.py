"""Regression guard for the Polymarket event-page parser.

2026-07: Polymarket stopped emitting an unescaped ``__NEXT_DATA__`` blob and now
serves the market JSON *escaped* inside the HTML — e.g. ``clobTokenIds\\":[\\"..``
instead of ``"clobTokenIds":["..``. The original regexes only matched the
unescaped form, so ``discover_markets`` silently returned ``found=0`` for ALL
windows (5m and 15m). These tests pin BOTH serialization styles.
"""
from quoter.markets import (
    _CONDITION_RE,
    _CONDITION_RE_ESC,
    _TOKEN_RE,
    _TOKEN_RE_ESC,
)

# A minimal slice of the current (escaped) event-page HTML. The backslashes are
# literal characters in the served bytes, so we double them in this Python source.
ESCAPED_HTML = (
    'foo\\"clobTokenIds\\":[\\"781380316598065282750942362550521257880342153209\\",'
    '\\"350048812215600000000000000000000000000000000000\\"]bar'
    'baz\\"conditionId\\":\\"0x30c85973b52052e65675ccf5490e5e9b64d1aa0772776b95\\"qux'
)

# The legacy unescaped form must still parse (defensive — Polymarket may A/B or revert).
UNESCAPED_HTML = (
    '"clobTokenIds":["111","222"]'
    '"conditionId":"0xabcdef0123456789"'
)


def test_escaped_tokens_parse():
    m = _TOKEN_RE_ESC.search(ESCAPED_HTML)
    assert m is not None
    assert m.group(1) == "781380316598065282750942362550521257880342153209"
    assert m.group(2) == "350048812215600000000000000000000000000000000000"
    assert m.group(1) != m.group(2)


def test_escaped_condition_parses():
    m = _CONDITION_RE_ESC.search(ESCAPED_HTML)
    assert m is not None
    assert m.group(1) == "0x30c85973b52052e65675ccf5490e5e9b64d1aa0772776b95"


def test_legacy_unescaped_still_parses():
    tm = _TOKEN_RE.search(UNESCAPED_HTML)
    assert tm is not None and (tm.group(1), tm.group(2)) == ("111", "222")
    cm = _CONDITION_RE.search(UNESCAPED_HTML)
    assert cm is not None and cm.group(1) == "0xabcdef0123456789"
