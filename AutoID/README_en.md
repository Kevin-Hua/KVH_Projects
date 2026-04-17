# AutoID — Auto Clock-In
> Automated attendance tool for CMS.  
> Opens Google Chrome via Playwright, loads a saved 30-day session,  
> and clicks **線上打卡** at scheduled times with a humanised random delay.

---

## Brief

| Item | Detail |
|------|--------|
| App name | RunTime (`_APP_NAME`) |
| Version | 1.0.1 |
| Config | `user.env` (CMS URL, credentials, SMTP, session selectors) |
| Language | Python 3.10+ |
| UI | tkinter — dark theme, no resize |
| Browser | Playwright → Google Chrome (`channel="chrome"`) |
| Session | 30-day CMS "Remember me" cookie |
| Schedule file | `autoid_schedule.json` |
| Options file | `autoid_options.json` |
| Session files | `autoid_session.json` + `autoid_session_ts.txt` |
| Log file | `log.log` |

---

## First Run

On first launch (no `user.env` found), a setup wizard opens to collect:

| Field | Notes |
|-------|-------|
| **CMS URL** | e.g. `https://cms.company.com/cms` |
| **Account** | Login email |
| **Password** | Login password |
| **Expired selector** | CSS selector visible on login page (default `#email`) |
| **Valid selector** | Clock-in button text on dashboard (default `線上打卡`) |
| **SMTP Host / Port / User / Password** | Optional — leave blank to disable email alerts |
| **Notify To** | Up to 5 comma-separated addresses |

The wizard writes `user.env` next to the executable. All fields can be edited later in `user.env` directly.

---

## user.env Reference

```ini
# Required
CMS_URL=https://cms.company.com/cms
EMAIL=user@company.com
PASSWORD=your-password

# Session selectors (change only if your CMS differs)
SESSION_EXPIRED_SEL=#email
SESSION_VALID_SEL=線上打卡

# SMTP email alerts (optional — leave blank to disable)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=sender@gmail.com
SMTP_PASS=app-password
NOTIFY_TO=you@example.com, colleague@example.com
```

> ⚠ `SESSION_EXPIRED_SEL` must be a CSS selector (e.g. `#email`). Do **not** put an email address here.

---

## Overview

```
┌──────────────────────────────────────────────┐
│  RunTime                          v1.0.0     │
│  ──────────────────────────────────────────  │
│  11:03                                       │  ← HH:MM  (minute-aligned refresh)
│  Session expires in 30d  (2026-05-10)        │  ← session expiry countdown
│  🔒 Next trigger: Fri 08:06  (in 21h)        │  ← next scheduled slot
│                                              │
│  [ Execute ]   [ Cancel ]   [ 🔒 ]           │  ← action buttons
│            [ 🔑 Auth Test ]                  │  ← session validity check
│                                              │
│  ┌ Set Schedule ──────────────────────────┐  │
│  │  Time 1:  08 : 06                      │  │
│  │  Time 2:  18 : 13                      │  │
│  │             [ ● ON ]                   │  │  ← schedule enable toggle
│  │  ◀  April 2026  ▶                      │  │
│  │  Sun Mon Tue Wed Thu Fri Sat           │  │  ← Sun-first calendar
│  │   green=ON  grey=OFF  red=weekend/ON   │  │
│  └────────────────────────────────────────┘  │
│                                              │
│  [● ON] Save log to log.log                  │
│  ┌ scrolled log ─────────────────────────┐   │
│  └───────────────────────────────────────┘   │
└──────────────────────────────────────────────┘
```

**Automatic sequence:**

1. App starts → load schedule + options + lock state
2. Scheduler watches `datetime.now()` for a slot match
3. Match found → compute random delay → start countdown
4. Countdown = 0 → open Chrome in a daemon thread → click 線上打卡
5. Browser stays open 3–10 min (random) → closes
6. Scheduler re-arms for the next slot

---

## Key Constants

| Constant | Value | Meaning |
|----------|-------|---------|
| `SESSION_DAYS` | `30` | Days before session is considered expired |
| `PRE_DELAY_MANUAL` | `30 s` | Countdown before a manual Execute fires |
| `CLOSE_MIN` | `(180, 600) s` | Random range the browser stays open after clicking |
| `POLL_INTERVAL_MS` | `30 000 ms` | Unlocked scheduler check interval |

---

## Options (`autoid_options.json`)

| Key | Default | Meaning |
|-----|---------|----------|
| `rand_enabled` | `true` | Enable random delay |
| `rand_min_min` | `1` | Minimum delay (minutes) |
| `rand_max_min` | `10` | Maximum delay (minutes) |
| `auto_renew_enabled` | `true` | Auto-renew session when cookies near expiry |
| `auto_renew_days` | `7` | Days before expiry to trigger auto-renew |
| `close_on_success` | `false` | Close app after successful clock-in |
| `close_on_success_secs` | `5` | Delay before closing app |
| `log_enabled` | `true` | Write to `log.log` |
| `startup_check_enabled` | `true` | Check session on startup |
| `startup_check_min` | `3` | Minutes before slot to run pre-check |
| `browser_close_enabled` | `true` | Close browser after clock-in |
| `browser_close_secs` | `10` | Seconds to keep browser open after click |
| `auto_close_timeout_enabled` | `false` | Auto-close browser after timeout |
| `auto_close_timeout_min` | `15` | Timeout in minutes |
| `2fa_timeout_secs` | `8` | Seconds to wait for dashboard after login before assuming 2FA required |

---

## Algorithm

### Random Delay (`_calc_delay`)

```
rand_enabled = True
    delay = randint(min_min, max_min) × 60  +  randint(0, 59)   seconds
    e.g.  min=1, max=10  →  range: 60 s … 659 s

rand_enabled = False
    delay = randint(0, 59)   seconds
```

Delay is computed **at the moment the slot is detected** (not in advance).

### Next-Slot Scan (`_next_fire_dt`)

```
slots = [(t1h,t1m), (t2h,t2m)]

for days_ahead = 0 … 7:
    d = today + days_ahead
    if not is_day_on(d): skip

    for (h, m) in sorted(slots):          ← earlier time wins
        dt = datetime(d, h, m)
        if dt > now: return dt

return None   (schedule OFF or no enabled day in 8-day window)
```

`is_day_on(d)` → checks `overrides[d.isoformat()]` dict first, falls back to `weekday < 5`.

### Slot Detection (`_tick_schedule`)

```
purge _fired_keys older than today's date prefix

if enabled AND today is ON:
    key = "YYYY-MM-DD HH:MM"
    if key NOT in _fired_keys:
        for (h, m) in slots:
            if now.hour == h AND now.minute == m:
                _fired_keys.add(key)
                _do_fire()
                break

re-arm:
    locked   → after(ms until next :00)   aligned to minute boundary
    unlocked → after(30 000)              every 30 seconds
```

`_fired_keys` is a `set[str]` — prevents double-trigger within the same slot minute even if the poll fires multiple times that minute.

---

## Flow Charts

### Startup

```
python AutoID.py
    └─ user.env missing?
          YES → _run_setup_wizard() → write user.env → restart
          NO  → _load_credentials() → EMAIL, PASSWORD, CMS_URL, SEL_EXPIRED, SEL_VALID
                App.__init__()
                      ├─ _load_sched()  → {t1h, t1m, t2h, t2m, enabled, locked, overrides}
                      ├─ _load_opts()   → {rand_enabled, min_min, max_min, 2fa_timeout_secs …}
                      ├─ Build UI widgets
                      ├─ _tick_clock()       ───► loops forever (minute-aligned UI refresh)
                      ├─ _tick_schedule()    ───► loops forever (scheduler poll)
                      └─ sched["locked"]==True ?
                               YES → _toggle_lock()  (restore lock state + disable controls)
```

### Schedule Fire Chain

```
_tick_schedule()
    │  slot matched
    ▼
_do_fire()
    ├── delay = _calc_delay(opts)
    ├── exec_dt = now + delay
    ├── log("triggered … exec at HH:MM:SS")
    └── _on_execute(pre_delay=delay, scheduled=True)
              │
              ├── disable Execute btn, enable Cancel btn
              ├── _exec_target_dt = now + delay
              └── _run_countdown()  ◄──────────────────┐
                        │                              │
                        ├── cancelled? → _reset_btns() │
                        ├── remaining ≤ 0? → _launch() │
                        └── update label               │
                            after(Δms) ────────────────┘
                                │
                                ▼  (remaining = 0)
                        _launch()   [daemon thread spawned]
                                │
                                ▼
                        clock_in(log, set_cd, stop, close_secs)
                            ├── Open Chrome (visible window)
                            ├── Load autoid_session.json if exists
                            ├── Navigate → https://cms.company.com.tw/cms/
                            ├── If #email visible:
                            │       fill email, focus password
                            │       wait for AutoID (user completes 2FA)
                            │       save session → autoid_session.json
                            ├── Wait for AutoID button (15 s timeout)
                            ├── Click AutoID
                            ├── Wait random close_secs  (checking stop every 1 s)
                            └── browser.close()
```

### Lock Toggle

```
User clicks 🔒 / 🔓
    │
    ▼
_toggle_lock()
    ├── flip _lock_var
    │
    ├── locked=True  → DISABLE: spinboxes, ON/OFF btn, day buttons, Options menu
    │   locked=False → ENABLE:  all above
    │
    ├── _refresh_lock_btn()    → update colour + emoji
    ├── update window title    → add/remove 🔒 prefix
    ├── save {locked} → autoid_schedule.json
    │
    ├── cancel current _poll_id
    └── _tick_schedule()
              locked   → next tick at :00 boundary  (~1 min precision)
              unlocked → next tick in 30 s
```

### Auth Test

```
User clicks 🔑 Auth Test
    │
    ▼
_test_auth()  [daemon thread]
    ├── Launch Chrome HEADLESS
    ├── Load autoid_session.json as storage state
    ├── Navigate → CMS_URL
    ├── SEL_EXPIRED visible?
    │       True  → expired = True
    │       False → expired = False
    ├── browser.close()
    └── _auth_done(expired)
              None  → ⚠ could not reach CMS  (dark red button)
              True  → ❌ Session Expired      (RED_ON button)
              False → ✅ Session Valid        (GREEN_ON button)
              after(5 000 ms) → reset button to default
```

### Session Auto-Renew

```
_bg_session_check()  [runs N min before each scheduled slot]
    ├── Navigate → CMS_URL  (headless)
    ├── Wait for SEL_PRECHECK  (= SEL_EXPIRED OR SEL_CLOCKIN)
    ├── SEL_EXPIRED visible?
    │       YES → send alert email "session expired pre-check"
    │       NO  → check cookie lifetime → near expiry?
    │                   YES and auto_renew_enabled → _auto_renew_session()
    └── browser.close()

_auto_renew_session()
    ├── Already expired? → log + abort
    └── _create_session()  → headless Phase 1 + visible Phase 2 (2FA)
```

---

## Scheduler Behaviour

| Aspect | Unlocked 🔓 | Locked 🔒 |
|--------|------------|---------|
| Poll interval | 30 s (fixed constant) | Aligned to next `HH:MM:00` boundary |
| Slot detection accuracy | 0 – 30 s after slot time | < 1 s after slot time |
| Random delay generated | At detection moment | At detection moment |
| Countdown label ticks | Every 1 second | Minute-aligned (same as clock) |
| UI controls | Fully editable | All disabled |
| Next-fire icon | 🔄 | 🔒 |
| Lock state persisted | ✅ saved to JSON | ✅ restored on startup |

### Countdown Label Format

```
Unlocked (1 s ticks):   "Executing in 4m 37s …"
Locked   (min aligned): "Executing in 4 min …"
                        "Executing in < 1 min …"  (last minute)
```

### Clock Tick Alignment

Both `_tick_clock` and `_tick_schedule` (locked) compute:

```python
ms_next = (60 - now.second) * 1000 - now.microsecond // 1000
after(max(ms_next, 500), callback)
```

This fires at the real `:00` second boundary, keeping clock and scheduler in sync.

---

## Potential Issues

| # | Issue | Notes |
|---|-------|-------|
| 1 | **Machine sleep** | `after()` pauses; resumes late. No missed-slot detection. Keep machine awake. |
| 2 | **Delay = 0 possible** | `randint(0,59)` can return 0 when random disabled. Browser fires at exactly `:00`. |
| 3 | **Session expiry** | Pre-check sends alert email. Run Auth Test / Renew Auth monthly. |
| 4 | **No public holidays** | Calendar defaults to weekday=ON. Toggle cells manually. |
| 5 | **Only 2 time slots** | Set both to same time if only 1 clock-in per day needed. |
| 6 | **2FA timeout too short** | Increase `2fa_timeout_secs` in `autoid_options.json` if login is slow. |

---

## File Reference

| File | Role |
|------|------|
| `AutoID.py` | Main application |
| `user.env` | Credentials + CMS URL + session selectors + SMTP — ⚠ keep private |
| `autoid_schedule.json` | `{t1h, t1m, t2h, t2m, enabled, locked, overrides:{}}` |
| `autoid_options.json` | All options including `2fa_timeout_secs` |
| `autoid_session.json` | Playwright storage state — ⚠ contains login cookies |
| `autoid_session_ts.txt` | Plain text ISO date of last session save |
| `log.log` | Append-only timestamped execution log |
| `Rewolf.ico` | Window icon |
# AutoID ClockIn — Auto Clock-In Tool

Automated attendance system for Company CMS (`https://cms.company.com.tw/cms/`).  
Launches a real Chrome browser via Playwright, logs in with a saved 30-day session,  
and clicks the **AutoID** button at configurable times with a random delay offset.

---

## Table of Contents

1. [Overview](#overview)
2. [File Inventory](#file-inventory)
3. [Dependencies](#dependencies)
4. [Constants & Configuration Variables](#constants--configuration-variables)
5. [Colour Palette](#colour-palette)
6. [Data Files](#data-files)
7. [Module-Level Functions](#module-level-functions)
8. [Class: App](#class-app)
   - [State Variables](#state-variables)
   - [UI Build Methods](#ui-build-methods)
   - [Scheduler Methods](#scheduler-methods)
   - [Execution Methods](#execution-methods)
   - [Lock Methods](#lock-methods)
   - [Utility Methods](#utility-methods)
9. [Class: _OptionsDialog](#class-_optionsdialog)
10. [Algorithm Flow Charts](#algorithm-flow-charts)
11. [Scheduler Behaviour by Mode](#scheduler-behaviour-by-mode)
12. [Potential Issues](#potential-issues)
13. [Reminders](#reminders)
14. [Future Plan](#future-plan)

---

## Overview

```
┌─────────────────────────────────────────────────────┐
│  AutoID ClockIn                              v1.0.1   │
│  11:03                                              │
│  Session expires in 30d  (2026-05-10)               │
│  🔒 Next trigger: Fri 08:06  (in 21h)  [+1–10 min] │
│                                                     │
│  [ Execute ]   [ Cancel ]   [ 🔒 ]                  │
│            [ 🔑 Auth Test ]                         │
│                                                     │
│  ┌ Set Schedule ────────────────────────────────┐   │
│  │  Time 1:  08 : 06                            │   │
│  │  Time 2:  18 : 13                            │   │
│  │              [ ● ON ]                        │   │
│  │  ◀  April 2026  ▶                            │   │
│  │  Sun Mon Tue Wed Thu Fri Sat                 │   │
│  │  ...  calendar buttons ...                   │   │
│  └──────────────────────────────────────────────┘   │
│                                                     │
│  [● ON] Save log to log.log                         │
│  ┌ scrolled log ─────────────────────────────────┐  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

**Automatic flow:**
1. App starts → loads saved schedule + options + lock state
2. Scheduler watches the clock for Time 1 / Time 2 slots
3. On match → compute random delay → start countdown
4. Countdown reaches 0 → open Chrome → navigate to CMS → click 線上打卡
5. Browser stays open for a random duration → closes automatically

---

## File Inventory

| File | Purpose |
|------|---------|
| `AutoID.py` | Main application (current stable) |
| `user.env` | Credentials + CMS URL + selectors + SMTP — ⚠ keep private |
| `autoid_schedule.json` | Persisted schedule: times, enabled, locked, overrides |
| `autoid_options.json` | All options including `2fa_timeout_secs` |
| `autoid_session.json` | Playwright browser storage state (cookies, localStorage) |
| `autoid_session_ts.txt` | ISO date when session was last saved |
| `log.log` | Timestamped execution log |
| `Rewolf.ico` | Application icon |

---

## Dependencies

```
Python  ≥ 3.10   (uses X | Y union types, match not used)
playwright       pip install playwright
                 playwright install chromium   (uses system Chrome via channel="chrome")
tkinter          built-in (Python standard library)
```

No other third-party packages required.

---

## Constants & Configuration Variables

### Runtime paths

| Constant | Value | Description |
|----------|-------|-------------|
| `URL_BASE` | `CMS_URL + "/"` | Dashboard URL (derived from `user.env`) |
| `URL_LOGIN` | `CMS_URL + "/login"` | Login URL (derived from `user.env`) |
| `SEL_EXPIRED` | from `user.env` | CSS selector visible when session expired |
| `SEL_CLOCKIN` | from `user.env` | Selector for clock-in button on dashboard |
| `SEL_PRECHECK` | `SEL_EXPIRED, SEL_CLOCKIN` | Combined selector for pre-check wait |
| `EMAIL` | from `user.env` | Login email |
| `HERE` | `os.path.dirname(__file__)` | Directory of the script itself |
| `SESSION_FILE` | `HERE/autoid_session.json` | Playwright storage state |
| `SESSION_TS` | `HERE/autoid_session_ts.txt` | Date session was last saved |
| `SCHEDULE_FILE` | `HERE/autoid_schedule.json` | Schedule persistence |
| `OPTIONS_FILE` | `HERE/autoid_options.json` | Options persistence |
| `LOG_FILE` | `HERE/log.log` | Append-only execution log |

### Timing

| Constant | Default | Description |
|----------|---------|-------------|
| `SESSION_DAYS` | `30` | Days before session considered expired |
| `PRE_DELAY_MANUAL` | `30` s | Countdown before manual Execute fires |
| `CLOSE_MIN` | `(180, 600)` s | Random range for browser auto-close after clicking |
| `POLL_INTERVAL_MS` | `30 000` ms | Scheduler check interval in unlocked mode |

### App metadata

| Constant | Description |
|----------|-------------|
| `_APP_VERSION` | Displayed in title bar |
| `_APP_NAME` | Window title prefix |
| `_APP_COPYRIGHT` | Reserved |
| `_ICO_FILE` | Tries `HERE/Rewolf.ico` first, then parent directory |

---

## Colour Palette

| Name | Hex | Used for |
|------|-----|---------|
| `BG` | `#2b2b2b` | Main window background |
| `BG_PANEL` | `#1e1e1e` | Spinbox / log background |
| `FG` | `#d4d4d4` | Default text |
| `CYAN` | `#56d1ff` | Clock, spinbox values, active menu |
| `GOLD` | `#f0c040` | Next-fire label, countdown, lock button, today border |
| `GREEN` | `#27ae60` | Schedule ON button, Auth Valid |
| `GREEN_ON` | `#1e7a40` | Weekday calendar day ON state |
| `RED_HDR` | `#9b2335` | Sun/Sat column headers |
| `RED_ON` | `#c0392b` | Weekend calendar day ON state |
| `BLUE` | `#1e6fb5` | Execute button |
| `GREY_OFF` | `#3a3a3a` | Calendar day OFF state |
| `GREY_PAST` | `#252525` | Past calendar day background |
| `FG_PAST` | `#555555` | Past calendar day text |
| `FG_NUM` | `#ffffff` | Future calendar day text (readable when disabled) |
| `TODAY_BDR` | `#f0c040` | Today's calendar cell highlight border |

---

## Data Files

### `autoid_schedule.json`
```json
{
  "t1h": 8,   "t1m": 6,
  "t2h": 18,  "t2m": 13,
  "enabled": true,
  "locked": false,
  "overrides": {
    "2026-04-11": false,
    "2026-04-14": true
  }
}
```
`overrides` keys are ISO date strings. Value `true`/`false` overrides the weekday default.

### `autoid_options.json`
```json
{
  "rand_enabled": true,
  "rand_min_min": 1,
  "rand_max_min": 10,
  "auto_renew_enabled": true,
  "auto_renew_days": 7,
  "2fa_timeout_secs": 8
}
```

### `autoid_session_ts.txt`
Plain text, single line: `2026-04-10`

---

## Module-Level Functions

### `_default_on(d: date) → bool`
Returns `True` if `d` is Monday–Friday (weekday < 5). Used as the base calendar state.

### `_load_sched() → dict`
Reads `autoid_schedule.json`. Applies backward-compat migration if old format detected  
(`"hour"/"minute"` keys → `"t1h"/"t1m"`). Falls back to safe defaults if file missing or corrupt.

### `_save_sched(data: dict)`
Writes the full schedule dict to `autoid_schedule.json` with 2-space indentation.

### `_load_opts() → dict`
Reads `autoid_options.json`. Falls back to `{rand_enabled: True, rand_min_min: 1, rand_max_min: 10}`.

### `_save_opts(opts: dict)`
Writes options dict to `autoid_options.json`.

### `_write_log(msg: str)`
Appends `[YYYY-MM-DD HH:MM:SS] msg\n` to `log.log`. Silent on failure.

### `_calc_delay(opts: dict) → int`
Computes the pre-execution random delay in **seconds**.

```
rand_enabled = True:
    delay = randint(min_min, max_min) × 60  +  randint(0, 59)
    range: [min_min×60 .. max_min×60+59] seconds

rand_enabled = False:
    delay = randint(0, 59)
    range: [0 .. 59] seconds
```

⚠ Can return `0` when disabled (both randint calls return 0).

### `clock_in(log, set_cd, stop: Event, close_secs: int | None, tfa_timeout_ms: int)`
Runs in a **daemon thread**. All UI callbacks go through `log()` and `set_cd()`.

```
Launch Chrome (non-headless, channel="chrome")
  → Load session if autoid_session.json exists
  → Navigate to CMS_URL
  → If SEL_EXPIRED visible:
       Fill email + password, click Submit
       Wait SEL_CLOCKIN (tfa_timeout_ms)
         success → save session
         timeout → send alert email, wait indefinitely for SEL_CLOCKIN
  → Wait for SEL_CLOCKIN button (15 s timeout)
  → Click it
  → Wait for random close_secs (3–10 min default), checking stop event
  → Close browser
```

---

## Class: App

Inherits `tk.Tk`. Single-window application.

### State Variables

| Variable | Type | Description |
|----------|------|-------------|
| `_cancel_flag` | `threading.Event` | Set to abort a running countdown |
| `_stop_flag` | `threading.Event` | Set to stop the browser worker thread |
| `_countdown_id` | `str \| None` | tkinter `after()` handle for countdown |
| `_exec_target_dt` | `datetime` | Absolute time when browser should fire |
| `_sched` | `dict` | In-memory copy of schedule (mirrors file) |
| `_opts` | `dict` | In-memory copy of options (mirrors file) |
| `_overrides` | `dict[str, bool]` | Calendar day overrides (subset of `_sched`) |
| `_view_year` | `int` | Calendar display year |
| `_view_month` | `int` | Calendar display month (1–12) |
| `_day_btns` | `dict[str, Button]` | ISO-date → calendar button widget |
| `_fired_keys` | `set[str]` | `"YYYY-MM-DD HH:MM"` strings already triggered |
| `_poll_id` | `str \| None` | Active scheduler `after()` handle |
| `_lock_var` | `tk.BooleanVar` | Lock state (True = locked) |
| `_en_var` | `tk.BooleanVar` | Schedule enabled state |
| `_spinboxes` | `list` | All time spinbox widgets (disabled when locked) |
| `_log_file_var` | `tk.BooleanVar` | Whether to write to log.log |

### UI Build Methods

| Method | Description |
|--------|-------------|
| `_build_menu()` | Creates menubar with `Tools → Options…`. Initialises `_lock_var=False`. |
| `_build_clock(p)` | Creates `_clock_lbl` (HH:MM), `_sess_lbl` (session expiry), `_next_lbl` (next fire) |
| `_build_cd_label(p)` | Creates `_cd_lbl` (gold countdown label) |
| `_build_exec_buttons(p)` | Creates Execute / Cancel / Lock (🔒) buttons + Auth Test button |
| `_build_schedule(p)` | Creates time spinboxes, ON/OFF toggle, calendar |
| `_build_calendar()` | Rebuilds the monthly calendar grid (destroyed and recreated on month nav) |
| `_build_log(p)` | Creates log-to-file toggle + `ScrolledText` log box |

### Scheduler Methods

| Method | Description |
|--------|-------------|
| `_tick_clock()` | Runs every minute (aligned to `:00`). Updates clock, session label, next-fire label. |
| `_tick_schedule()` | Unified scheduler. Polls every 30 s (unlocked) or minute-aligned (locked). Fires `_do_fire()` on slot match. |
| `_read_slots() → list \| None` | Reads + clamps spinbox values to valid ranges. Returns `None` on parse error. |
| `_next_fire_dt() → datetime \| None` | Scans up to 8 days ahead. Returns next valid slot `datetime`. Uses `sorted(slots)` so earlier slot wins. |
| `_eta_str(target) → str` | Formats time-to-target as `"Xh Ym"`, `"Ym"`, or `"< 1 min"`. |
| `_delay_label() → str` | Returns human-readable delay range string for the next-fire label. |
| `_next_fire_str() → str` | Formats the full next-fire label text. Shows 🔒 icon when locked. |
| `_refresh_next_lbl()` | Calls `_next_fire_str()` and updates the label widget. |
| `_do_fire(delay=None)` | Computes delay if not given. Logs. Calls `_on_execute(pre_delay, scheduled=True)`. |

### Execution Methods

| Method | Description |
|--------|-------------|
| `_on_execute(pre_delay, scheduled)` | Disables Execute, enables Cancel. Sets `_exec_target_dt`. Starts `_run_countdown()`. |
| `_run_countdown()` | Recursive `after()`loop. Locked→minute ticks; unlocked→1 s ticks. Calls `_launch()` at zero. |
| `_launch()` | Spawns daemon thread running `clock_in(...)`. Calls `_reset_btns()` when done. |
| `_on_cancel()` | Sets both flags, cancels `_countdown_id`, resets UI. |
| `_reset_btns()` | Re-enables Execute, disables Cancel, clears countdown label. |

### Lock Methods

| Method | Description |
|--------|-------------|
| `_toggle_lock()` | Flips `_lock_var`. Disables/enables all editable controls. Saves lock state. Restarts scheduler. |
| `_refresh_lock_btn()` | Updates lock button appearance (text, bg, fg). |

### Utility Methods

| Method | Description |
|--------|-------------|
| `_refresh_sess_label()` | Reads `autoid_session_ts.txt`, computes days remaining, colours label red/orange/green. |
| `_is_day_on(d) → bool` | Checks `_overrides` dict first; falls back to `_default_on(d)`. |
| `_refresh_day_btn(key, is_wkend, on)` | Sets calendar button colour (green/red/grey). |
| `_toggle_day(key)` | Flips a day override. No-op if locked or past. Saves schedule. |
| `_prev_month() / _next_month()` | Adjusts `_view_year`/`_view_month`, rebuilds calendar. |
| `_clamp_spinbox(var, max_val)` | FocusOut/Return handler: clamps to [0, max_val], zero-pads, saves. |
| `_toggle_enable()` | Flips `_en_var`, refreshes button, saves. |
| `_refresh_en_btn()` | Updates enable button text/colour. |
| `_toggle_log_file() / _refresh_log_file_btn()` | Toggles file logging. |
| `_log_msg(msg)` | Appends to `_log_box` via `after(0)`. Writes to file if enabled. |
| `_set_cd(msg)` | Thread-safe update of countdown label via `after(0)`. |
| `_test_auth()` | Spawns headless Chrome, checks `SEL_EXPIRED` visible → session valid/expired. |
| `_auth_done(expired)` | Updates Auth Test button. Resets after 5 s. |
| `_save()` | No-op if locked. Clamps all spinbox values, writes full schedule dict to file. |
| `_open_options()` | Opens `_OptionsDialog`. |
| `_on_opts_saved(opts)` | Stores new opts, saves to file. |

---

## Class: _OptionsDialog

`tk.Toplevel`, modal (`grab_set()`), always-on-top.

| Widget | Description |
|--------|-------------|
| Enable toggle button | Toggles `rand_enabled`. Greys out spinboxes when OFF. |
| Min delay spinbox | `rand_min_min` — minimum random minutes (0–60) |
| Max delay spinbox | `rand_max_min` — maximum random minutes (≥ min) |
| Note label | Reminds user seconds are always random 00–59 |
| Save / Cancel | Save clamps min≤max, calls `_on_opts_saved`. |

---

## Algorithm Flow Charts

### Startup

```
main()
  └─ user.env missing? → _run_setup_wizard() → write user.env
  └─ _load_credentials() → EMAIL, PASSWORD, CMS_URL, SEL_EXPIRED, SEL_VALID
  └─ App.__init__()
       ├─ _load_sched()  ──► _sched (times, enabled, locked, overrides)
       ├─ _load_opts()   ──► _opts  (rand_enabled, min_min, max_min, 2fa_timeout_secs…)
       ├─ _build_* UI
       ├─ _tick_clock()          ← starts minute-aligned UI refresh loop
       ├─ _tick_schedule()       ← starts 30s poll loop
       └─ if sched["locked"]:
            _toggle_lock()       ← restores lock state, switches to minute poll
```

### Scheduler Tick

```
_tick_schedule()
  ├─ Purge _fired_keys older than today
  ├─ if enabled AND today is ON:
  │    now = datetime.now()
  │    key_min = "YYYY-MM-DD HH:MM"
  │    if key_min NOT in _fired_keys:
  │      slots = _read_slots() [clamped]
  │      for (h, m) in slots:
  │        if now.hour==h AND now.minute==m:
  │          if exec_btn is NORMAL:
  │            _fired_keys.add(key_min)
  │            _do_fire()          ← trigger!
  │          break
  └─ Re-arm:
       locked → after(ms_to_next_:00)
       unlocked → after(30_000)
```

### Fire & Execute

```
_do_fire(delay=None)
  ├─ delay = _calc_delay(opts)   [if not provided]
  │     rand_enabled → randint(min,max)×60 + randint(0,59)
  │     disabled     → randint(0,59)
  ├─ exec_dt = now + timedelta(seconds=delay)
  ├─ _log_msg("triggered … delay … exec at …")
  └─ _on_execute(pre_delay=delay, scheduled=True)

_on_execute(n)
  ├─ Disable Execute, Enable Cancel
  ├─ _exec_target_dt = now + timedelta(seconds=n)
  └─ _run_countdown()

_run_countdown()   [recursive after() loop]
  ├─ if cancelled → _reset_btns()
  ├─ remaining = _exec_target_dt - now
  ├─ if remaining ≤ 0 → _launch()
  ├─ locked   → after(ms_to_next_:00) → _run_countdown()
  └─ unlocked → after(1000)           → _run_countdown()

_launch()
  └─ Thread: clock_in(log, set_cd, stop_flag, close_secs, tfa_timeout_ms)
       ├─ Open Chrome + load/save session
       ├─ Navigate to CMS_URL
       ├─ SEL_EXPIRED visible? → login flow with 2FA detection
       ├─ Click 線上打卡
       ├─ Wait random close_secs (checking stop_flag every 1 s)
       └─ Close browser
```

### Lock Toggle

```
_toggle_lock()
  ├─ Flip _lock_var
  ├─ Disable/Enable: spinboxes, en_btn, log_btn, day_btns, Options menu
  ├─ Update title bar (🔒 prefix)
  ├─ Save locked state → autoid_schedule.json
  ├─ Cancel existing _poll_id
  └─ _tick_schedule()   ← re-arms with new interval
```

### Auth Test

```
_test_auth()  [daemon thread]
  ├─ Launch headless Chrome
  ├─ Load autoid_session.json as storage state
  ├─ Navigate to CMS_URL
  ├─ Check: SEL_EXPIRED visible?
  │     True  → session expired
  │     False → session valid
  └─ _auth_done(expired)
       ├─ expired=True  → ❌ red button
       ├─ expired=False → ✅ green button
       └─ expired=None  → error state
       └─ after(5000) → reset button text
```

---

## Scheduler Behaviour by Mode

| | Unlocked | Locked |
|---|---|---|
| Poll interval | 30 s (fixed) | Aligned to next `:00` boundary (~1 min) |
| Detection accuracy | 0–30 s after slot | 0–1 s after slot |
| Delay generated | At detection moment | At detection moment |
| Delay visible in advance | No | No |
| UI controls | Editable | All disabled |
| Countdown ticks | Every 1 second | Minute-aligned (same as clock) |
| Scheduler label icon | 🔄 | 🔒 |

---

## Potential Issues

### 1. Machine sleep / hibernate
`tkinter.after()` pauses when the machine sleeps and resumes late. In unlocked mode the 30 s poll may fire many minutes late. In locked mode the minute-aligned poll similarly drifts. **No missed-slot detection is implemented.**

*Mitigation:* Keep machine awake (disable sleep in Windows power settings).

### 2. Delay can be 0 seconds
When random delay is **disabled**, `_calc_delay()` returns `randint(0, 59)`. If both calls return 0, the browser fires at exactly `:00` seconds — the same second as the slot detection.

*Mitigation:* Change `randint(0, 59)` to `randint(1, 59)`.

### 3. Fired-key set grows unboundedly (minor)
`_fired_keys` is purged only for keys not matching today's date prefix. After midnight the old keys are dropped. On very long uptimes with many slots this is negligible.

### 4. Session expiry
A 1-hour pre-check runs before each slot. If `SEL_EXPIRED` is detected, an alert email is sent (if SMTP is configured). Run **Auth Test** monthly or use **Renew Auth** to refresh the session.

### 5. Spinbox direct keyboard entry
Despite keystroke validation (digits only, max 2 chars), a user can still type `99` in the hour field. `_clamp_spinbox` only fires on FocusOut/Return. If the user leaves the field by clicking elsewhere (some focus transitions), clamping may not trigger.

*Mitigation:* `_read_slots()` provides a final clamp at schedule-read time.

### 6. Holiday / public holiday awareness
Calendar defaults to weekday=ON, weekend=OFF. There is no automatic public holiday handling. Chinese New Year, national holidays etc. must be manually toggled in the calendar.

### 7. Tcl integer limit (theoretical)
`tkinter.after()` internally uses a 32-bit signed integer on some Windows builds, limiting to ~24.8 days. The scheduler re-arms every ~1 minute so this limit is never approached.

### 8. Two time slots only
The current design supports exactly two time slots (Time 1, Time 2). If only one clock-in per day is needed, set both to the same time or set Time 2 to a non-working hour — it will be skipped.

---

## Reminders

- **First run:** Setup wizard fires when `user.env` is missing. Fill in CMS URL, email, password, and optionally SMTP settings. The wizard writes `user.env` next to the executable.
- **Session renewal:** Run **Auth Test** monthly. If expired, click **Renew Auth** to re-authenticate.
- **2FA:** If 2FA is required during clock-in, the browser stays open. Complete 2FA manually (check "Remember me 30 days"). An alert email is sent if SMTP is configured.
- **Lock before leaving:** Press 🔒 to enter locked mode. The schedule, times, and calendar cannot be accidentally changed. Lock state persists across restarts.
- **Log file:** Green **● ON** button next to "Save log to log.log" enables disk logging. The in-app log box always shows messages regardless of this toggle.
- **Build EXE:**
  ```
  python -m PyInstaller --onefile --noconsole --name "AutoID_ClockIn" AutoID.py
  ```
  Playwright browsers must still be installed separately on the target machine.
- **`autoid_session.json` is sensitive** — it contains all browser cookies and localStorage. Do not commit it to version control.

---

## Future Plan

| Priority | Feature |
|----------|---------|
| High | Pre-show exact exec time in label (pre-generate delay at lock time) |
| High | Missed-slot detection (check if machine woke from sleep late) |
| Medium | Public holiday calendar (import from `.ics` or government API) |
| Medium | System tray minimisation (hide to tray instead of taskbar) |
| Medium | Notification pop-up / Windows toast when clock-in fires |
| Medium | Session auto-renewal prompt (warn 5 days before expiry) |
| Low | Support more than 2 time slots |
| Low | PyInstaller spec file with Playwright bundled |
| Low | Dark/light theme toggle |
| Low | Log viewer with filter/search |
| Low | Export attendance log to CSV |
