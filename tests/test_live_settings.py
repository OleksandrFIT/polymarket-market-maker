import json
import pytest
from quoter.config import Config
from quoter.ops.live_settings import LiveSettings, ALLOWED_KEYS


def _ls(tmp_path):
    return LiveSettings(Config(), path=str(tmp_path / "settings.json"))


def test_load_missing_file(tmp_path):
    ls = _ls(tmp_path)
    ls.load()
    assert ls.snapshot() == {}


def test_load_valid(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"per_market_cap_usd": 15.0, "flat_size": 7}))
    ls = LiveSettings(Config(), path=str(p))
    ls.load()
    assert ls.snapshot() == {"per_market_cap_usd": 15.0, "flat_size": 7}


def test_load_corrupt_ignored(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{not json")
    ls = LiveSettings(Config(), path=str(p))
    ls.load()
    assert ls.snapshot() == {}


def test_update_valid_persists(tmp_path):
    p = tmp_path / "settings.json"
    ls = LiveSettings(Config(), path=str(p))
    ls.update("per_market_cap_usd", 15)
    assert ls.snapshot()["per_market_cap_usd"] == 15.0
    assert json.loads(p.read_text())["per_market_cap_usd"] == 15.0


def test_update_out_of_range_rejected(tmp_path):
    ls = _ls(tmp_path)
    with pytest.raises(ValueError):
        ls.update("favorite_min_price", 1.5)
    assert "favorite_min_price" not in ls.snapshot()


def test_update_unknown_key_rejected(tmp_path):
    ls = _ls(tmp_path)
    with pytest.raises(ValueError):
        ls.update("bankroll_usd", 999)


def test_update_wrong_type_rejected(tmp_path):
    ls = _ls(tmp_path)
    with pytest.raises(ValueError):
        ls.update("flat_size", "abc")


def test_snapshot_is_copy(tmp_path):
    ls = _ls(tmp_path)
    ls.update("flat_size", 7)
    snap = ls.snapshot()
    snap["flat_size"] = 999
    assert ls.snapshot()["flat_size"] == 7


def test_min_price_above_max_warns(tmp_path):
    ls = _ls(tmp_path)
    ls.update("max_entry_price", 0.90)
    res = ls.update("favorite_min_price", 0.95)
    assert "warning" in res


def test_effective_has_all_keys(tmp_path):
    ls = _ls(tmp_path)
    eff = ls.effective()
    assert set(eff) == set(ALLOWED_KEYS)
    assert eff["favorite_min_price"] == Config().favorite_min_price


def test_update_bool_rejected(tmp_path):
    ls = _ls(tmp_path)
    with pytest.raises(ValueError):
        ls.update("per_market_cap_usd", True)


def test_update_returns_all_effective_keys(tmp_path):
    ls = _ls(tmp_path)
    res = ls.update("flat_size", 7)
    for k in ALLOWED_KEYS:
        assert k in res
    assert res["flat_size"] == 7


def test_lottery_knob_validates(tmp_path):
    ls = _ls(tmp_path)
    ls.update("lottery_max_price", 0.30)
    assert ls.snapshot()["lottery_max_price"] == 0.30
    with pytest.raises(ValueError):
        ls.update("lottery_max_price", 0.9)  # > 0.49 range


def test_momentum_inverted_band_warns(tmp_path):
    ls = _ls(tmp_path)
    ls.update("momentum_max_price", 0.55)
    res = ls.update("momentum_min_price", 0.60)  # min >= max → empty band
    assert "warning" in res
