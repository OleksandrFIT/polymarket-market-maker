"""Runtime-editable overlay for the tactical strategy knobs.

The base Config is frozen and loaded once. LiveSettings holds overrides for a
whitelisted subset of tactical knobs; the quoter loop overlays a snapshot on the
base config each requote via dataclasses.replace, so dashboard edits take effect
from the next tick without a restart. Overrides persist to a JSON file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quoter.config import Config
from quoter.ops.logger import get_logger

log = get_logger("live_settings")

# key -> (type, min, max)
_SPEC: dict[str, tuple[type, float, float]] = {
    "per_market_cap_usd": (float, 1.0, 500.0),
    "favorite_min_price": (float, 0.50, 0.99),
    "max_entry_price": (float, 0.50, 0.99),
    "entry_start_frac": (float, 0.0, 0.95),
    "flat_size": (int, 1, 200),
    "rise_tolerance_cents": (float, 0.0, 0.10),
    "favorite_ladder_levels": (int, 1, 10),
    "velocity_confirm_threshold": (float, 0.0, 0.01),
    "min_time_to_expiry_sec": (float, 1.0, 60.0),
    "lottery_max_price": (float, 0.10, 0.49),
    "lottery_cap_usd": (float, 0.0, 50.0),
    "lottery_size": (int, 0, 50),
    "lottery_levels": (int, 0, 5),
}

ALLOWED_KEYS = tuple(_SPEC.keys())


class LiveSettings:
    """Mutable overlay of tactical knob overrides, persisted to JSON."""

    def __init__(self, base: Config, path: str = "settings.json") -> None:
        self._base = base
        self._path = Path(path)
        self._overrides: dict[str, Any] = {}

    def _coerce(self, key: str, value: Any) -> Any:
        if key not in _SPEC:
            raise ValueError(
                f"unknown key {key!r}; allowed: {', '.join(ALLOWED_KEYS)}"
            )
        typ, lo, hi = _SPEC[key]
        if isinstance(value, bool):  # bool is an int subclass — reject explicitly
            raise ValueError(f"{key} must be {typ.__name__}")
        try:
            num = typ(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be {typ.__name__}")
        if not (lo <= num <= hi):
            raise ValueError(f"{key} must be in [{lo}, {hi}]")
        return num

    def load(self) -> None:
        """Read overrides from disk; ignore a missing or corrupt file."""
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
        except Exception as e:
            log.warning("live_settings_load_failed", error=str(e))
            return
        clean: dict[str, Any] = {}
        for k, v in (data or {}).items():
            try:
                clean[k] = self._coerce(k, v)
            except ValueError:
                log.warning("live_settings_drop_bad_key", key=k)
        self._overrides = clean

    def update(self, key: str, value: Any) -> dict[str, Any]:
        """Validate + store + persist one override. Raises ValueError on bad input.

        Returns the new override dict, possibly with a 'warning' field.
        """
        num = self._coerce(key, value)
        self._overrides[key] = num
        self._persist()
        result: dict[str, Any] = self.effective()
        warn = self._band_warning()
        if warn:
            result["warning"] = warn
        return result

    def _band_warning(self) -> str | None:
        eff = self.effective()
        if eff["favorite_min_price"] > eff["max_entry_price"]:
            return "empty band — no trades (favorite_min_price > max_entry_price)"
        return None

    def _persist(self) -> None:
        try:
            self._path.write_text(json.dumps(self._overrides, indent=2))
        except Exception as e:
            log.warning("live_settings_persist_failed", error=str(e))

    def snapshot(self) -> dict[str, Any]:
        """Shallow copy of current overrides for per-tick overlay."""
        return dict(self._overrides)

    def effective(self) -> dict[str, Any]:
        """Effective value of every whitelisted knob (base default unless overridden)."""
        return {k: self._overrides.get(k, getattr(self._base, k)) for k in ALLOWED_KEYS}
