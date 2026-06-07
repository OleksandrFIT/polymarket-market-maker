# Phase-A Live-Editable Dashboard Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Edit the 9 tactical strategy knobs live from the dashboard; changes apply next
requote without restart and persist to `settings.json`. Paper only; do NOT touch live trading.

**Architecture:** A mutable `LiveSettings` overlay (whitelisted + range-validated) holds
overrides; the quoter loop overlays a snapshot on the frozen base `Config` each requote via
`dataclasses.replace`; the dashboard adds GET/POST `/api/settings` (mirroring the existing
`set_risk` pattern) and a Settings form.

**Tech:** Python 3.13, aiohttp dashboard, pytest. Test: `.venv/bin/pytest <path> -v`. Branch: master.

**Spec:** `docs/superpowers/specs/2026-06-07-phaseA-live-settings-design.md`

---

## Task 1: `LiveSettings` class + unit tests

**Files:** Create `quoter/ops/live_settings.py`; Create `tests/test_live_settings.py`.

- [ ] **Step 1: Write the test file `tests/test_live_settings.py`:**

```python
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
    ls.load()  # must not raise
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
```

- [ ] **Step 2: Run, expect fail.** `.venv/bin/pytest tests/test_live_settings.py -v` → FAIL (module missing).

- [ ] **Step 3: Create `quoter/ops/live_settings.py`:**

```python
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
        result: dict[str, Any] = dict(self._overrides)
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
```

- [ ] **Step 4: Run, expect pass.** `.venv/bin/pytest tests/test_live_settings.py -v` → PASS.

- [ ] **Step 5: Commit.**
```bash
git add quoter/ops/live_settings.py tests/test_live_settings.py
git commit -m "feat(phase-A): LiveSettings overlay with whitelist + range validation + persistence"
```

---

## Task 2: Quoter-loop overlay + main.py wiring

**Files:** Modify `quoter/quoter_loop.py`; Modify `quoter/main.py`; Modify `tests/test_quoter_loop.py`.

- [ ] **Step 1: Add a test in `tests/test_quoter_loop.py`** that the loop overlays live
  settings onto the base cfg. Add (adapt construction to the file's existing helper/fixtures —
  read how the existing test builds a QuoterLoop and mirror it):

```python
def test_live_settings_overlay_reaches_strategy(monkeypatch):
    """A live per_market_cap_usd override must be applied to the effective cfg
    passed into compute_ladder (proves no-restart tuning)."""
    import quoter.quoter_loop as ql
    captured = {}
    def fake_compute_ladder(cfg, **kw):
        captured["cap"] = cfg.per_market_cap_usd
        return []
    monkeypatch.setattr(ql, "compute_ladder", fake_compute_ladder)
    # Build a loop the same way the other test in this file does, then:
    loop.live.update("per_market_cap_usd", 15)
    # drive one requote for a market with a valid book/mid (reuse the file's setup)
    # then:
    assert captured["cap"] == 15.0
```
  If wiring a full requote is impractical in this test file, instead assert the overlay
  directly: `assert replace(loop.cfg, **loop.live.snapshot()).per_market_cap_usd == 15.0`
  after `loop.live.update("per_market_cap_usd", 15)`. Keep it meaningful (exercises the real
  overlay), not trivially true.

- [ ] **Step 2: Run, expect fail** (`loop.live` doesn't exist yet).

- [ ] **Step 3: Edit `quoter/quoter_loop.py`.**
  (a) Add the import at the top: `from dataclasses import replace` (and
  `from quoter.ops.live_settings import LiveSettings`).
  (b) In `QuoterLoop.__init__`, add a parameter `live: LiveSettings | None = None` and store
  `self.live = live if live is not None else LiveSettings(cfg)`.
  (c) In `_requote_market`, where it currently calls `compute_ladder(self.cfg, ...)`, build the
  effective config first and pass it instead:
```python
            eff_cfg = replace(self.cfg, **self.live.snapshot())
            desired = compute_ladder(
                eff_cfg,
                mid_yes=mid_yes,
                time_to_expiry=tte,
                prev_mid_yes=self._prev_mid_yes.get(market_id),
                committed_side=committed,
                inventory_yes_qty=yes_qty,
                inventory_no_qty=no_qty,
                timeframe=market.timeframe,
                asset=market.asset,
                velocity_short=velo_short,
                velocity_long=velo_long,
            )
            self._prev_mid_yes[market_id] = mid_yes
            self.exec.sync(market_id, desired)
```
  (Only the first arg changes from `self.cfg` to `eff_cfg`; everything else stays.)

- [ ] **Step 4: Wire `main.py`.** Before `quoter = QuoterLoop(...)` (line ~189), construct and
  load the overlay:
```python
    live_settings = LiveSettings(cfg, path="settings.json")
    live_settings.load()
```
  Add `live=live_settings,` to the `QuoterLoop(...)` kwargs. Add the import at the top of
  main.py: `from quoter.ops.live_settings import LiveSettings  # noqa: E402` (match the file's
  existing import style). Keep `live_settings` in scope — Task 3 passes it to `make_app`.

- [ ] **Step 5: Run.** `.venv/bin/pytest tests/test_quoter_loop.py -v` → PASS.

- [ ] **Step 6: Commit.**
```bash
git add quoter/quoter_loop.py quoter/main.py tests/test_quoter_loop.py
git commit -m "feat(phase-A): overlay LiveSettings onto base cfg each requote"
```

---

## Task 3: Dashboard GET/POST /api/settings + form + persistence wiring

**Files:** Modify `quoter/ops/metrics.py`; Modify `quoter/main.py`; Modify
`quoter/ops/dashboard.py`; Modify `.gitignore`; Modify `tests/test_dashboard.py`.

- [ ] **Step 1: Add integration tests in `tests/test_dashboard.py`** (mirror how the file
  builds the app via `make_app` and the aiohttp test client; read its existing pattern and add
  a `live_settings=LiveSettings(cfg)` arg to the make_app call in the test setup):

```python
async def test_get_settings(aiohttp_client):
    # build app via the file's existing make_app helper, with live_settings
    client = await aiohttp_client(app)
    r = await client.get("/api/settings")
    assert r.status == 200
    data = await r.json()
    assert "per_market_cap_usd" in data and "favorite_min_price" in data


async def test_post_settings_applies(aiohttp_client):
    client = await aiohttp_client(app)
    r = await client.post("/api/settings", json={"key": "per_market_cap_usd", "value": 15})
    assert r.status == 200
    g = await (await client.get("/api/settings")).json()
    assert g["per_market_cap_usd"] == 15.0


async def test_post_settings_bad_400(aiohttp_client):
    client = await aiohttp_client(app)
    r = await client.post("/api/settings", json={"key": "favorite_min_price", "value": 9})
    assert r.status == 400
    r2 = await client.post("/api/settings", json={"key": "nope", "value": 1})
    assert r2.status == 400
    # app still serves
    assert (await client.get("/api/settings")).status == 200
```

- [ ] **Step 2: Run, expect fail** (no /api/settings route).

- [ ] **Step 3: Edit `quoter/ops/metrics.py`.**
  (a) Import: `from quoter.ops.live_settings import LiveSettings`.
  (b) Add `live_settings: LiveSettings` to the `make_app(*, ...)` keyword params.
  (c) Add two handlers (place near `set_risk`):
```python
    async def get_settings(_request: web.Request) -> web.Response:
        return web.json_response(live_settings.effective())

    async def post_settings(request: web.Request) -> web.Response:
        try:
            body = await request.json()
            key = body["key"]
            value = body["value"]
        except Exception:
            return web.json_response(
                {"error": "expected JSON {key, value}"}, status=400
            )
        try:
            result = live_settings.update(key, value)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response(result)
```
  (d) Register routes (next to the others):
```python
    app.router.add_get("/api/settings", get_settings)
    app.router.add_post("/api/settings", post_settings)
```

- [ ] **Step 4: Wire `main.py`.** In the `make_app(...)` call (line ~232), add
  `live_settings=live_settings,` (the variable created in Task 2).

- [ ] **Step 5: Add the Settings form to `quoter/ops/dashboard.py`.** Following the existing
  risk-control card pattern (an input + Apply button + status span, with a JS `setRisk()`
  posting to `/api/risk`), add a Settings card after the risk card. Insert this HTML block
  (place it right after the risk `<div class="row" ...>...</div>` block that contains
  `clearAll()`):

```html
<h2>Tactic settings (live — applies next window)</h2>
<div class="card" id="settings-card">
  <div id="settings-fields" class="row" style="flex-wrap: wrap; gap: 12px;"></div>
  <span id="settings-status" class="muted"></span>
</div>
```

  And add this JS (near the existing `setRisk` function):

```javascript
const SETTING_KEYS = [
  "per_market_cap_usd","favorite_min_price","max_entry_price","entry_start_frac",
  "flat_size","rise_tolerance_cents","favorite_ladder_levels",
  "velocity_confirm_threshold","min_time_to_expiry_sec"
];
let settingsLoaded = false;
async function loadSettings() {
  const r = await fetch("/api/settings");
  const s = await r.json();
  const box = document.getElementById("settings-fields");
  box.innerHTML = "";
  for (const k of SETTING_KEYS) {
    const wrap = document.createElement("div");
    wrap.style.cssText = "display:flex;flex-direction:column;gap:2px;";
    wrap.innerHTML = `<label class="muted" style="font-size:11px;">${k}</label>`;
    const inp = document.createElement("input");
    inp.type = "number"; inp.step = "any"; inp.id = "set-" + k; inp.value = s[k];
    inp.style.width = "120px";
    const btn = document.createElement("button");
    btn.className = "fbtn"; btn.textContent = "Apply";
    btn.onclick = () => setSetting(k);
    const rowEl = document.createElement("div");
    rowEl.style.cssText = "display:flex;gap:4px;";
    rowEl.appendChild(inp); rowEl.appendChild(btn);
    wrap.appendChild(rowEl); box.appendChild(wrap);
  }
  settingsLoaded = true;
}
async function setSetting(key) {
  const val = document.getElementById("set-" + key).value;
  const st = document.getElementById("settings-status");
  const r = await fetch("/api/settings", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({key, value: parseFloat(val)})
  });
  const d = await r.json();
  if (r.status !== 200) { st.textContent = "❌ " + d.error; st.style.color = "#e66"; }
  else if (d.warning) { st.textContent = "⚠ " + d.warning; st.style.color = "#ec6"; }
  else { st.textContent = "✓ " + key + " = " + val; st.style.color = "#6e6"; }
}
```

  Call `loadSettings()` ONCE on page load (add to the existing init / first-load path; do NOT
  call it on the 2s auto-refresh, so it never clobbers a field mid-edit).

- [ ] **Step 6: `.gitignore`.** Append a line: `settings.json`.

- [ ] **Step 7: Run dashboard tests + FULL suite.**
  `.venv/bin/pytest tests/test_dashboard.py -v` → PASS.
  `.venv/bin/pytest -q` → ALL green.

- [ ] **Step 8: Commit.**
```bash
git add quoter/ops/metrics.py quoter/main.py quoter/ops/dashboard.py .gitignore tests/test_dashboard.py
git commit -m "feat(phase-A): /api/settings endpoints + dashboard tactic-settings form"
```

---

## Self-Review
- **Spec coverage:** LiveSettings + whitelist + ranges + persistence (Task 1); per-requote
  overlay + no-restart (Task 2); GET/POST endpoints + form + validation 400 + warning + base
  stays frozen (Task 3). ✅
- **Placeholders:** the test files for Task 2/3 reference "the file's existing make_app/loop
  setup" — this is deliberate (the implementer mirrors existing fixtures rather than
  duplicating unknown setup); all NEW logic has complete code. ✅
- **Type consistency:** `LiveSettings(base, path)` constructor, `snapshot()`, `effective()`,
  `update(key,value)`, `ALLOWED_KEYS` used consistently across tasks. ✅
- **Scope:** paper only; no live-trading code; base Config stays frozen. ✅
