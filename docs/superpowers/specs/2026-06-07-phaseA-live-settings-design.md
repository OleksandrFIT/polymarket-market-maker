# Phase-A: Runtime-Editable Strategy Settings (Dashboard) — Design Spec

**Date:** 2026-06-07
**Status:** Approved (all 3 sections approved by user)
**Goal:** Let the user change the 9 tactical strategy knobs live from the dashboard; changes
apply from the NEXT requote without restarting the bot, and persist across restart.

**Scope constraint:** Paper only. No live-trading code. Markets/assets are out of scope.

---

## 1. Architecture & components

Base `Config` stays a frozen dataclass. A mutable `LiveSettings` overlay holds overrides for
ONLY the 9 whitelisted tactical knobs. Each requote, the quoter loop overlays the snapshot on
the base config via `dataclasses.replace` and passes the effective config to `compute_ladder`
— so changes take effect from the next tick/window without restart.

**Components:**

1. **`quoter/ops/live_settings.py`** (new) — `LiveSettings` class:
   - holds `_overrides: dict[str, float|int]` (only whitelisted keys)
   - `snapshot() -> dict` — returns a shallow copy for safe per-tick overlay
   - `update(key, value) -> dict` — validate + range-check + store + write `settings.json`;
     raises `ValueError` (→ HTTP 400) on bad key/type/range; returns the new effective state
     (incl. an optional `warning` field)
   - `load(path)` — at startup, read `settings.json` if present; corrupt file → log + ignore
   - `effective(base_cfg) -> dict` — base default for every knob, overridden where set
   - WHITELIST + ranges per §2.

2. **`quoter/quoter_loop.py`** — add `self.live: LiveSettings`; in `_requote_market`, before
   computing the ladder:
   ```python
   from dataclasses import replace
   eff_cfg = replace(self.cfg, **self.live.snapshot())
   desired = compute_ladder(eff_cfg, mid_yes=mid_yes, time_to_expiry=tte,
                            prev_mid_yes=self._prev_mid_yes.get(market_id), ...)
   ```
   The base `self.cfg` is never mutated.

3. **`quoter/ops/metrics.py`** — mirror the existing `set_risk` POST pattern:
   - `GET /api/settings` → JSON of the 9 effective values
   - `POST /api/settings` with JSON `{key, value}` → call `LiveSettings.update`; 200 with the
     new state (+ optional `warning`), or 400 with a clear message on `ValueError`.
   - `make_app(...)` gains a `live_settings` parameter.

4. **`quoter/ops/dashboard.py`** — add a "Settings" section to `HTML_DASHBOARD`: a form with
   the 9 fields (populated from `GET /api/settings`), an "Apply" button per field (or one
   Apply), and a status line showing success / 400-error / warning. The 2s auto-refresh must
   NOT clobber a field the user is editing (only repopulate on load / after Apply).

5. **`settings.json`** (repo root, next to `state.db`) — persists overrides; add to
   `.gitignore`.

6. **`quoter/main.py`** — construct `LiveSettings(path="settings.json")`, call `load()`, and
   pass it into both `QuoterLoop` and `make_app`.

---

## 2. Data flow, validation, error handling

**Data flow:**
- Startup: `LiveSettings.load()` reads `settings.json` → overrides (empty if no file).
- Each requote: `eff_cfg = replace(base_cfg, **snapshot)` → `compute_ladder(eff_cfg, …)`.
- `POST /api/settings {key, value}`: validate → update overlay → write `settings.json` →
  200 with new state.
- `GET /api/settings`: effective value of all 9 (base default where no override).

**Thread-safety:** dashboard and quoter loop run in the same asyncio loop (cooperative, no OS
threads), so `snapshot()` returning a shallow copy is race-free.

**Whitelist + ranges (reject out-of-range with HTTP 400 — NOT silent clamp):**

| Key | Type | Range |
|---|---|---|
| `per_market_cap_usd` | float | 1 – 500 |
| `favorite_min_price` | float | 0.50 – 0.99 |
| `max_entry_price` | float | 0.50 – 0.99 |
| `entry_start_frac` | float | 0.0 – 0.95 |
| `flat_size` | int | 1 – 200 |
| `rise_tolerance_cents` | float | 0.0 – 0.10 |
| `favorite_ladder_levels` | int | 1 – 10 |
| `velocity_confirm_threshold` | float | 0.0 – 0.01 |
| `min_time_to_expiry_sec` | float | 1 – 60 |

**Error handling (all → HTTP 400 with a clear message; bot never crashes):**
- Bad JSON / unknown key / wrong type / out-of-range → 400 + "allowed X–Y".
- Soft warning (non-blocking, 200): if `favorite_min_price > max_entry_price`, return the new
  state with `warning: "empty band — no trades"`.
- `settings.json` write failure → log, still apply in-memory (don't fail the update).
- Startup with corrupt `settings.json` → log warning, ignore (use config defaults), no crash.

---

## 3. Testing & success criteria

**Unit (`tests/test_live_settings.py`, new):**
| Test | Asserts |
|---|---|
| `test_load_missing_file` | no settings.json → empty overrides |
| `test_load_valid` | valid file → overrides applied |
| `test_load_corrupt_ignored` | corrupt JSON → ignored, no raise |
| `test_update_valid_persists` | valid value → stored + written to file |
| `test_update_out_of_range_rejected` | out of range → ValueError, state unchanged |
| `test_update_unknown_key_rejected` | non-whitelist key → ValueError |
| `test_update_wrong_type_rejected` | non-numeric → ValueError |
| `test_snapshot_is_copy` | mutating the snapshot doesn't affect the holder |
| `test_min_price_above_max_warns` | favorite_min_price > max_entry_price → warning flag |

**Integration (`tests/test_dashboard.py`, extend; aiohttp test client):**
| Test | Asserts |
|---|---|
| `test_get_settings` | GET /api/settings → all 9 effective values |
| `test_post_settings_applies` | POST valid → 200, GET reflects the new value |
| `test_post_settings_bad_400` | out-of-range / unknown key → 400, app still serves |
| `test_effective_cfg_changes_behavior` | `replace(base, **snapshot)` with a low cap →
  `compute_ladder` returns `[]` (cap fires), proving the overlay reaches the strategy |

**Success criteria:**
1. Changing a knob on the dashboard → the next requote uses it (no restart).
2. Persists across restart (`settings.json`).
3. Invalid input → 400, bot keeps running.
4. Base `Config` stays frozen; full suite green.

**Out of scope:** live trading (paper only); markets/assets editing; auto-tuning (user tunes
manually).

## 4. Sources
- Existing runtime-mutation precedent: `set_risk` POST endpoint in `quoter/ops/metrics.py`.
- phase-16 tactic (the knobs being exposed): `compute_ladder` in `quoter/strategy/ladder.py`.
