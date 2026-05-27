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
        assert c.ladder_levels == 50  # Phase-9 Bonereaper-clone continuous coverage

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
