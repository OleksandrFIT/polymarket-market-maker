"""Tests for Config + PolyCreds loading."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from quoter.config import Config
from quoter.creds import PolyCreds


class TestConfig:
    def test_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            c = Config.from_env()
        assert c.mode == "shadow"
        assert c.is_shadow
        assert not c.is_paper and not c.is_live
        assert c.bankroll_usd == 100.0

    def test_mode_paper(self):
        with patch.dict(os.environ, {"MODE": "paper"}, clear=True):
            c = Config.from_env()
        assert c.is_paper and not c.is_shadow

    def test_mode_live(self):
        with patch.dict(os.environ, {"MODE": "live"}, clear=True):
            c = Config.from_env()
        assert c.is_live

    def test_invalid_mode_raises(self):
        with patch.dict(os.environ, {"MODE": "bogus"}, clear=True), pytest.raises(ValueError):
            Config.from_env()

    def test_bankroll_override(self):
        with patch.dict(os.environ, {"BANKROLL": "500"}, clear=True):
            c = Config.from_env()
        assert c.bankroll_usd == 500.0

    def test_log_level_uppercased(self):
        with patch.dict(os.environ, {"LOG_LEVEL": "debug"}, clear=True):
            c = Config.from_env()
        assert c.log_level == "DEBUG"

    def test_frozen(self):
        from dataclasses import FrozenInstanceError
        c = Config()
        with pytest.raises(FrozenInstanceError):
            c.bankroll_usd = 999  # type: ignore[misc]

    def test_phase16_defaults(self):
        c = Config()
        assert c.favorite_min_price == 0.85
        assert c.max_entry_price == 0.97
        assert c.entry_start_frac == 0.60
        assert c.flat_size == 10
        assert c.per_market_cap_usd == 50.0
        assert c.certainty_cap_multiplier == 2.0
        assert c.velocity_confirm_threshold == 0.0005
        assert c.rise_tolerance_cents == 0.01
        assert c.favorite_ladder_levels == 3
        assert c.min_time_to_expiry_sec == 5.0


class TestPolyCreds:
    GOOD_ENV = {
        "POLY_PRIVATE_KEY": "0xabcd",
        "POLY_API_KEY": "key-uuid",
        "POLY_API_SECRET": "c2VjcmV0",
        "POLY_API_PASSPHRASE": "phrase",
        "POLY_FUNDER_ADDRESS": "0xfunder",
        "POLY_SIGNATURE_TYPE": "1",
    }

    def test_loads_all_fields(self):
        with patch.dict(os.environ, self.GOOD_ENV, clear=True):
            c = PolyCreds.from_env()
        assert c.private_key == "0xabcd"
        assert c.api_key == "key-uuid"
        assert c.funder == "0xfunder"
        assert c.sig_type == 1

    def test_missing_required_raises(self):
        env = dict(self.GOOD_ENV)
        del env["POLY_API_SECRET"]
        with patch.dict(os.environ, env, clear=True), pytest.raises(RuntimeError, match="POLY_API_SECRET"):
            PolyCreds.from_env()

    def test_funder_optional(self):
        env = dict(self.GOOD_ENV)
        del env["POLY_FUNDER_ADDRESS"]
        with patch.dict(os.environ, env, clear=True):
            c = PolyCreds.from_env()
        assert c.funder == ""

    def test_auth_dict_format(self):
        with patch.dict(os.environ, self.GOOD_ENV, clear=True):
            c = PolyCreds.from_env()
        auth = c.auth_dict()
        assert auth == {"apiKey": "key-uuid", "secret": "c2VjcmV0", "passphrase": "phrase"}
