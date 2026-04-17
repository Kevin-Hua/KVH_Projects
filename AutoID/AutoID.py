#!/usr/bin/env python
"""
Auto Clock-In  v1
═══════════════════════════════════════════════════════
• Live clock + session expiry + next-fire countdown
• Execute / Cancel buttons + Lock button
• Set Schedule: two trigger times + monthly calendar
• Unlocked → polls every 30 s using datetime.now()
• Locked   → exact after() timer to slot + pre-gen delay
• Tools → Options: enable/disable random delay, set minute range
  (seconds component always random 00–59)
• Session stored in autoid_session.json (valid 30 days via 2FA)
"""

import calendar as _cal
import datetime
import json
import os
import random
import smtplib
import sys
import threading
import time
import traceback
import tkinter as tk
from email.mime.text import MIMEText
from tkinter import scrolledtext
from playwright.sync_api import sync_playwright

# ── Constants ──────────────────────────────────────────────────────────────────
# URL_BASE and URL_LOGIN are derived from CMS_URL in user.env (see below).
# When packed as a one-file exe, use the exe's directory so that
# json/env files placed next to the exe are found correctly.
if getattr(sys, 'frozen', False):
    HERE = os.path.dirname(os.path.abspath(sys.executable))
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
SESSION_FILE     = os.path.join(HERE, "autoid_session.json")
SESSION_TS       = os.path.join(HERE, "autoid_session_ts.txt")
SESSION_DAYS     = 30
SCHEDULE_FILE    = os.path.join(HERE, "autoid_schedule.json")
OPTIONS_FILE     = os.path.join(HERE, "autoid_options.json")
LOG_FILE         = os.path.join(HERE, "log.log")
PRE_DELAY_MANUAL = 30          # seconds before manual Execute fires
CLOSE_MIN        = (3 * 60, 10 * 60)   # random browser-close delay range (s)
POLL_INTERVAL_MS = 30_000      # unlocked poll interval


# ── First-run setup wizard ────────────────────────────────────────────────────
def _run_setup_wizard():
    """Show a setup dialog to collect all user.env values on first run."""
    import tkinter as _tk

    _BG       = "#2b2b2b"
    _BG_PANEL = "#1e1e1e"
    _FG       = "#d4d4d4"
    _CYAN     = "#56d1ff"
    _GREEN    = "#27ae60"
    _RED      = "#c0392b"
    _LBL_W    = 16   # label column width (chars)
    _ENT_W    = 42   # entry width (chars)

    root = _tk.Tk()
    root.title("AutoID — First Run Setup")
    root.resizable(False, False)
    root.configure(bg=_BG)
    root.minsize(620, 0)

    saved = [False]

    def _section(text):
        _tk.Label(root, text=text, bg=_BG, fg=_CYAN,
                  font=("Segoe UI", 10, "bold"), anchor="w"
                  ).pack(fill=_tk.X, padx=20, pady=(14, 2))
        _tk.Frame(root, bg="#444444", height=1).pack(fill=_tk.X, padx=20, pady=(0, 6))

    def _row(label, default="", show="", note=""):
        """Create a label+entry row; returns the Entry widget."""
        f = _tk.Frame(root, bg=_BG)
        f.pack(fill=_tk.X, padx=20, pady=3)
        _tk.Label(f, text=label, bg=_BG, fg=_FG,
                  font=("Segoe UI", 9), width=_LBL_W, anchor="e"
                  ).pack(side=_tk.LEFT, padx=(0, 8))
        e = _tk.Entry(f, show=show, width=_ENT_W,
                      bg=_BG_PANEL, fg=_FG, insertbackground=_FG,
                      relief=_tk.FLAT, font=("Segoe UI", 10),
                      highlightthickness=1, highlightbackground="#444444")
        e.pack(side=_tk.LEFT)
        if default:
            e.insert(0, default)
        if note:
            _tk.Label(f, text=note, bg=_BG, fg="#666666",
                      font=("Segoe UI", 8)).pack(side=_tk.LEFT, padx=(8, 0))
        return e

    def _pw_row(label):
        """Create a password row with Show checkbox; returns the Entry widget."""
        f = _tk.Frame(root, bg=_BG)
        f.pack(fill=_tk.X, padx=20, pady=3)
        _tk.Label(f, text=label, bg=_BG, fg=_FG,
                  font=("Segoe UI", 9), width=_LBL_W, anchor="e"
                  ).pack(side=_tk.LEFT, padx=(0, 8))
        e = _tk.Entry(f, show="●", width=_ENT_W,
                      bg=_BG_PANEL, fg=_FG, insertbackground=_FG,
                      relief=_tk.FLAT, font=("Segoe UI", 10),
                      highlightthickness=1, highlightbackground="#444444")
        e.pack(side=_tk.LEFT)
        v = _tk.BooleanVar(value=False)
        _tk.Checkbutton(f, text="Show", variable=v,
                        bg=_BG, fg=_FG, selectcolor=_BG_PANEL,
                        activebackground=_BG, font=("Segoe UI", 8),
                        command=lambda: e.config(show="" if v.get() else "●")
                        ).pack(side=_tk.LEFT, padx=(8, 0))
        return e

    # ── Header ────────────────────────────────────────────────────────────────
    _tk.Label(root, text="AutoID — First Run Setup",
              bg=_BG, fg=_CYAN, font=("Segoe UI", 14, "bold")
              ).pack(pady=(18, 2))
    _tk.Label(root, text="These values will be saved to user.env next to the program.",
              bg=_BG, fg="#888888", font=("Segoe UI", 9)
              ).pack(pady=(0, 6))

    # ── Required ──────────────────────────────────────────────────────────────
    _section("Required")
    e_url   = _row("CMS URL *",   note="e.g. https://your-company.com/cms")
    e_email = _row("Account *")
    e_pw    = _pw_row("Password *")

    # ── Session Selectors ────────────────────────────────────────────────────
    _section("Session Selectors  (optional — change only if your CMS differs)")
    e_sel_expired = _row("Expired selector", default="#email",
                         note="CSS selector visible when session has expired")
    e_sel_valid   = _row("Valid selector",   default="\u7dda\u4e0a\u6253\u5361",
                         note="button text / input value on the dashboard")

    # ── Optional SMTP ─────────────────────────────────────────────────────────
    _section("Email Alerts  (optional — leave blank to disable)")
    e_smtp_host = _row("SMTP Host",     default="smtp.gmail.com")
    e_smtp_port = _row("SMTP Port",     default="587",  note="default 587")
    e_smtp_user = _row("SMTP User",     note="sender address")
    e_smtp_pass = _pw_row("SMTP Password")
    e_notify    = _row("Notify To",     note="up to 5 addresses, comma-separated")

    # ── SMTP help ─────────────────────────────────────────────────────────────
    f_help = _tk.Frame(root, bg="#1a1a2e", highlightthickness=1,
                       highlightbackground="#0078d4")
    f_help.pack(fill=_tk.X, padx=20, pady=(10, 4))
    _tk.Label(f_help, text="ℹ  Gmail App Password",
              bg="#1a1a2e", fg="#56d1ff", font=("Segoe UI", 8, "bold"),
              anchor="w").pack(anchor="w", padx=10, pady=(6, 2))
    _tk.Label(f_help,
              text="Gmail requires an App Password (not your regular password) when 2-Step\n"
                   "Verification is enabled.  To generate one:\n"
                   "  1. Go to  myaccount.google.com  →  Security\n"
                   "  2. Under \"2-Step Verification\" click  App passwords\n"
                   "  3. Select app: Mail, device: Windows Computer → Generate\n"
                   "  4. Paste the 16-character code into SMTP Password above.",
              bg="#1a1a2e", fg="#aaaaaa", font=("Consolas", 8),
              justify=_tk.LEFT, anchor="w"
              ).pack(anchor="w", padx=10, pady=(0, 8))

    # ── Session check help ────────────────────────────────────────────────────
    f_sess = _tk.Frame(root, bg="#1a2e1a", highlightthickness=1,
                       highlightbackground="#27ae60")
    f_sess.pack(fill=_tk.X, padx=20, pady=(4, 4))
    _tk.Label(f_sess, text="ℹ  How session validity is checked",
              bg="#1a2e1a", fg="#56d1ff", font=("Segoe UI", 8, "bold"),
              anchor="w").pack(anchor="w", padx=10, pady=(6, 2))
    _tk.Label(f_sess,
              text="After login, Playwright saves your browser cookies to  autoid_session.json.\n"
                   "Each session check works as follows:\n"
                   "  1. Load saved cookies into a headless Chrome instance.\n"
                   "  2. Navigate to CMS_URL — the dashboard page.\n"
                   "  3. If the  Expired selector  is visible  →  session EXPIRED.\n"
                   "     If the  Valid selector    is visible  →  session VALID.\n"
                   "  4. Cookie expiry is also read from the JSON (cookies with lifetime\n"
                   "     ≥ 30 days).  Auto-renew fires when ≤ N days remain (default 7 d).\n"
                   "  5. A pre-check runs 1 hour before each scheduled slot and sends an\n"
                   "     email alert if the session has already expired.",
              bg="#1a2e1a", fg="#aaaaaa", font=("Consolas", 8),
              justify=_tk.LEFT, anchor="w"
              ).pack(anchor="w", padx=10, pady=(0, 8))

    # ── Error label ───────────────────────────────────────────────────────────
    err_lbl = _tk.Label(root, text="", bg=_BG, fg=_RED, font=("Segoe UI", 9))
    err_lbl.pack(pady=(8, 0))

    # ── Buttons ───────────────────────────────────────────────────────────────
    err_lbl = _tk.Label(root, text="", bg=_BG, fg=_RED, font=("Segoe UI", 9))
    err_lbl.pack(pady=(8, 0))

    # ── Buttons ───────────────────────────────────────────────────────────────
    def _save():
        url   = e_url.get().strip().rstrip("/")
        email = e_email.get().strip()
        pw    = e_pw.get()
        if not url:
            err_lbl.config(text="CMS URL is required."); return
        if not email:
            err_lbl.config(text="Email is required."); return
        if not pw:
            err_lbl.config(text="Password is required."); return
        port_str = e_smtp_port.get().strip()
        port = int(port_str) if port_str.isdigit() else 587
        sel_exp_raw = e_sel_expired.get().strip()
        # Guard: an email address is not a valid CSS selector
        sel_exp   = sel_exp_raw if sel_exp_raw and "@" not in sel_exp_raw else "#email"
        sel_valid = e_sel_valid.get().strip() or "\u7dda\u4e0a\u6253\u5361"
        lines = [
            "# AutoID credentials\n",
            "# This file is read at startup — keep it private, do not commit to version control.\n",
            f"CMS_URL={url}\n",
            f"EMAIL={email}\n",
            f"PASSWORD={pw}\n",
            f"SESSION_EXPIRED_SEL={sel_exp}\n",
            f"SESSION_VALID_SEL={sel_valid}\n",
        ]
        smtp_host = e_smtp_host.get().strip()
        smtp_user = e_smtp_user.get().strip()
        smtp_pass = e_smtp_pass.get()
        notify    = e_notify.get().strip()
        if smtp_host or smtp_user or smtp_pass or notify:
            lines += [
                "\n# ── SMTP alert (optional) ────────────────────────────────────────────────────\n",
                f"SMTP_HOST={smtp_host or 'smtp.gmail.com'}\n",
                f"SMTP_PORT={port}\n",
                f"SMTP_USER={smtp_user}\n",
                f"SMTP_PASS={smtp_pass}\n",
            ]
            if notify:
                lines.append(f"NOTIFY_TO={notify}\n")
        with open(os.path.join(HERE, "user.env"), "w", encoding="utf-8") as _f:
            _f.writelines(lines)
        saved[0] = True
        root.destroy()

    btn_row = _tk.Frame(root, bg=_BG)
    btn_row.pack(pady=16)
    _tk.Button(btn_row, text="Save & Start", width=14,
               font=("Segoe UI", 9, "bold"),
               bg=_GREEN, fg="white", activebackground="#1e9a50",
               relief=_tk.FLAT, cursor="hand2",
               command=_save).pack(side=_tk.LEFT, padx=8)
    _tk.Button(btn_row, text="Cancel", width=10,
               font=("Segoe UI", 9),
               bg="#555555", fg="white", activebackground="#666666",
               relief=_tk.FLAT, cursor="hand2",
               command=root.destroy).pack(side=_tk.LEFT, padx=8)

    root.update_idletasks()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"+{(sw - root.winfo_width()) // 2}+{(sh - root.winfo_height()) // 2}")
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()

    if not saved[0]:
        sys.exit(0)


if not os.path.exists(os.path.join(HERE, "user.env")):
    _run_setup_wizard()


# ── Credentials (loaded from user.env) ────────────────────────────────────────
def _load_credentials() -> tuple[str, str, str, str, str]:
    """Read EMAIL, PASSWORD, CMS_URL, SESSION_EXPIRED_SEL and SESSION_VALID_SEL
    from user.env next to the script."""
    env_file = os.path.join(HERE, "user.env")
    email, password, cms_url = "", "", ""
    sel_expired = "#email"
    sel_valid   = "\u7dda\u4e0a\u6253\u5361"
    if os.path.exists(env_file):
        with open(env_file, encoding="utf-8") as _f:
            for line in _f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip()
                if key == "EMAIL":
                    email = val
                elif key == "PASSWORD":
                    password = val
                elif key == "CMS_URL":
                    cms_url = val.rstrip("/")
                elif key == "SESSION_EXPIRED_SEL":
                    sel_expired = val if "@" not in val else "#email"
                elif key == "SESSION_VALID_SEL":
                    sel_valid = val
    return email, password, cms_url, sel_expired, sel_valid

EMAIL, PASSWORD, _CMS_URL, SEL_EXPIRED, _SEL_VALID = _load_credentials()
if not _CMS_URL:
    import sys as _sys
    _sys.exit("ERROR: CMS_URL is not set in user.env")
URL_BASE   = _CMS_URL + "/"
URL_LOGIN  = _CMS_URL + "/login"
SEL_CLOCKIN  = f"input[value='{_SEL_VALID}'], button:has-text('{_SEL_VALID}')"
SEL_PRECHECK = f"{SEL_EXPIRED}, {SEL_CLOCKIN}"


# ── SMTP alert config (loaded from user.env) ─────────────────────────────────────────
def _load_smtp_config() -> dict:
    """Read SMTP_HOST/PORT/USER/PASS/NOTIFY_TO from user.env.
    Example entries to add to user.env:
        SMTP_HOST=smtp.gmail.com
        SMTP_PORT=587
        SMTP_USER=sender@gmail.com
        SMTP_PASS=app_password_here
        NOTIFY_TO=addr1@example.com, addr2@example.com   # up to 5, comma-separated
    Returns empty list for 'to' if keys are absent (alerts disabled)."""
    cfg = {"host": "smtp.gmail.com", "port": 587, "user": "", "password": "", "to": []}
    env_file = os.path.join(HERE, "user.env")
    if not os.path.exists(env_file):
        return cfg
    with open(env_file, encoding="utf-8") as _f:
        for line in _f:
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            if key == "SMTP_HOST":     cfg["host"]     = val
            elif key == "SMTP_PORT":   cfg["port"]     = int(val) if val.isdigit() else 587
            elif key == "SMTP_USER":   cfg["user"]     = val
            elif key == "SMTP_PASS":   cfg["password"] = val
            elif key == "NOTIFY_TO":
                addrs = [a.strip() for a in val.split(",") if a.strip()]
                cfg["to"] = addrs[:5]   # cap at 5 recipients
    return cfg

SMTP_CFG = _load_smtp_config()


def _send_alert_email(subject: str, body: str) -> bool:
    """Send an email alert via SMTP TLS.  Returns True on success.
    Silently does nothing if SMTP config is incomplete."""
    cfg = SMTP_CFG
    if not all([cfg["host"], cfg["user"], cfg["password"]]) or not cfg["to"]:
        return False
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"]    = cfg["user"]
        msg["To"]      = ", ".join(cfg["to"])
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as s:
            s.ehlo()
            s.starttls()
            s.login(cfg["user"], cfg["password"])
            s.sendmail(cfg["user"], cfg["to"], msg.as_string())
        return True
    except Exception:
        return False

# ── App Metadata ───────────────────────────────────────────────────────────────
_APP_VERSION   = "1.0.2"
_APP_NAME      = "RunTime"
_APP_COPYRIGHT = "\u00a9 2026 ThexehT"

# ── Icon ───────────────────────────────────────────────────────────────────────
_ICO_FILE = os.path.join(HERE, "Rewolf.ico")
if not os.path.exists(_ICO_FILE):
    _ICO_FILE = os.path.join(os.path.dirname(HERE), "Rewolf.ico")

# ── Colours ────────────────────────────────────────────────────────────────────
BG        = "#2b2b2b"
BG_PANEL  = "#1e1e1e"
FG        = "#d4d4d4"
CYAN      = "#56d1ff"
GOLD      = "#f0c040"
GREEN     = "#27ae60"
RED_HDR   = "#9b2335"
RED_ON    = "#c0392b"
BLUE      = "#1e6fb5"
GREY_OFF  = "#3a3a3a"
GREY_PAST = "#252525"
FG_PAST   = "#555555"
GREEN_ON  = "#1e7a40"
TODAY_BDR = "#f0c040"
FG_NUM    = "#ffffff"


# ── Helpers ────────────────────────────────────────────────────────────────────
def _default_on(d: datetime.date) -> bool:
    return d.weekday() < 5


def _load_sched() -> dict:
    base = {"t1h": 8, "t1m": 0, "t2h": 17, "t2m": 0,
            "enabled": False, "locked": False, "overrides": {}}
    if os.path.exists(SCHEDULE_FILE):
        try:
            data = json.loads(open(SCHEDULE_FILE).read())
            if "hour" in data and "t1h" not in data:
                data["t1h"] = data.pop("hour")
                data["t1m"] = data.pop("minute", 0)
                data.setdefault("t2h", 17); data.setdefault("t2m", 0)
            base.update(data)
        except Exception:
            pass
    return base


def _save_sched(data: dict):
    with open(SCHEDULE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _load_opts() -> dict:
    base = {"rand_enabled": True, "rand_min_min": 1, "rand_max_min": 10,
            "auto_renew_enabled": True, "auto_renew_days": 7,
            "close_on_success": False, "log_enabled": True,
            "startup_check_enabled": True, "startup_check_min": 3,
            "browser_close_enabled": True, "browser_close_secs": 10,
            "close_on_success_secs": 5,
            "auto_close_timeout_enabled": False, "auto_close_timeout_min": 15,
            "2fa_timeout_secs": 8}
    if os.path.exists(OPTIONS_FILE):
        try:
            base.update(json.loads(open(OPTIONS_FILE).read()))
        except Exception:
            pass
    return base


def _save_opts(opts: dict):
    with open(OPTIONS_FILE, "w") as f:
        json.dump(opts, f, indent=2)


def _write_log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


# ── Random delay calculation ───────────────────────────────────────────────────
def _calc_delay(opts: dict) -> int:
    """Return pre-execution delay in seconds based on options.
    Always adds random 0-59 seconds.
    If rand_enabled: adds rand_min_min .. rand_max_min random minutes on top."""
    secs = random.randint(0, 59)
    if opts.get("rand_enabled", True):
        lo = max(0, opts.get("rand_min_min", 1))
        hi = max(lo, opts.get("rand_max_min", 10))
        secs += random.randint(lo, hi) * 60
    return secs


# ── Automation ────────────────────────────────────────────────────────────────
def clock_in(log, set_cd, stop: threading.Event,
             close_secs: int | None = None,
             on_success=None,
             tfa_timeout_ms: int = 8_000):
    if close_secs is not None:
        log(f"[clockin] Browser close in {close_secs}s after click.")
    else:
        log(f"[clockin] Browser close: random {CLOSE_MIN[0]//60}–{CLOSE_MIN[1]//60} min.")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False, channel="chrome")

            if os.path.exists(SESSION_FILE):
                log("Loading saved session …")
                ctx = browser.new_context(storage_state=SESSION_FILE)
            else:
                log("No saved session — please log in manually.")
                ctx = browser.new_context()

            page = ctx.new_page()
            page.on("dialog", lambda dlg: (log(f"Dialog: {dlg.message}"), dlg.accept()))

            log("Navigating to CMS …")
            page.goto(URL_BASE, wait_until="domcontentloaded")

            if page.locator(SEL_EXPIRED).is_visible():
                log("Session expired — attempting auto-login …")
                page.fill("#email", EMAIL)
                if PASSWORD:
                    page.fill("#password", PASSWORD)
                    log("Credentials filled. Submitting …")
                    page.locator(
                        "button[type='submit'], input[type='submit']"
                    ).first.click()
                    try:
                        # Fast path: no 2FA required
                        page.wait_for_selector(SEL_CLOCKIN, timeout=tfa_timeout_ms)
                        log("Login succeeded (no 2FA).")
                    except Exception:
                        # 2FA needed — let the user complete it
                        log("2FA required — complete it in the browser window.")
                        log("(Check 'Remember me 30 days' then submit 2FA.)")
                        _send_alert_email(
                            "[AutoID] ⚠ 2FA required — action pending",
                            "AutoID attempted to clock in but 2FA is required.\n"
                            "Please complete 2FA in the browser window to proceed.\n"
                            "(Check 'Remember me 30 days' before submitting.)")
                        log("Alert email sent for 2FA prompt.")
                        page.wait_for_selector(SEL_CLOCKIN, timeout=0)
                        log("2FA complete.")
                else:
                    page.focus("#password")
                    log("No password in user.env — enter manually.")
                    _send_alert_email(
                        "[AutoID] ⚠ Manual login required — action pending",
                        "AutoID attempted to clock in but no password is configured.\n"
                        "Please complete the login in the browser window to proceed.")
                    log("Alert email sent for manual login prompt.")
                    page.wait_for_selector(SEL_CLOCKIN, timeout=0)
                log("Saving session …")
                ctx.storage_state(path=SESSION_FILE)
                with open(SESSION_TS, "w") as _f:
                    _f.write(datetime.date.today().isoformat())
                log("Session saved.")

            log(f"Dashboard: {page.url}")
            log(f"Looking for {_SEL_VALID} button …")
            btn = page.locator(SEL_CLOCKIN)
            btn.first.wait_for(state="visible", timeout=15_000)
            log(f"Clicking {_SEL_VALID} …")
            btn.first.click()

            page.wait_for_timeout(3_000)
            log("Clock-in click confirmed.")
            if on_success:
                on_success()   # signal success NOW — before the browser countdown

            total = close_secs if close_secs is not None else random.randint(*CLOSE_MIN)
            m, s = divmod(total, 60)
            log(f"Browser will close in {m}m {s}s …")
            for rem in range(total, 0, -1):
                if stop.is_set():
                    break
                if not browser.is_connected():
                    log("Browser was closed manually — skipping countdown.")
                    break
                rm, rs = divmod(rem, 60)
                set_cd(f"Browser closing in {rm}m {rs:02d}s …")
                time.sleep(1)
            set_cd("")
            if browser.is_connected():
                browser.close()
                log("Browser closed. Done!")
            else:
                log("Browser already closed. Done!")
            return True

    except Exception as exc:
        log(f"ERROR: {exc}")
        log(traceback.format_exc().strip())
        return False


# ── GUI ────────────────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{_APP_NAME}  v{_APP_VERSION}")
        self.resizable(False, False)
        self.configure(bg=BG)
        if os.path.exists(_ICO_FILE):
            try:
                self.iconbitmap(_ICO_FILE)
            except Exception:
                pass

        self._cancel_flag  = threading.Event()
        self._stop_flag    = threading.Event()
        self._countdown_id: str | None = None
        self._sched        = _load_sched()
        self._opts         = _load_opts()
        self._overrides: dict[str, bool] = self._sched.get("overrides", {})

        today = datetime.date.today()
        self._view_year  = today.year
        self._view_month = today.month
        self._day_btns:  dict[str, tk.Button] = {}

        # scheduler state
        self._fired_keys: set = set()    # "YYYY-MM-DD HH:MM" → already fired
        self._pre_checked_keys: set = set()  # "YYYY-MM-DD HH:MM" → pre-check done
        self._poll_id:    str | None = None
        self._auto_close_timer_id: str | None = None
        self._timeout_tick_id: str | None = None
        self._timeout_deadline: datetime.datetime | None = None
        self._lock_var:   tk.BooleanVar   # created in _build_menu
        self._auto_renew_date: datetime.date | None = None  # last auto-renew attempt
        self._expiry_cache: datetime.date | None = None       # cached cookie expiry
        self._expiry_cache_day: datetime.date | None = None   # date cache was built

        self._build_menu()           # must be first — creates _lock_var

        outer = tk.Frame(self, bg=BG, padx=12, pady=10)
        outer.pack(fill=tk.BOTH, expand=True)

        self._build_clock(outer)
        self._build_cd_label(outer)
        self._build_exec_buttons(outer)
        self._build_schedule(outer)
        self._build_log(outer)

        self._tick_clock()
        self._tick_schedule()
        # restore lock state from last session
        if self._sched.get("locked", False):
            self._toggle_lock()
        # sync log toggle from saved opts
        self._log_file_var.set(self._opts.get("log_enabled", True))
        self._refresh_log_file_btn()
        # run session test on startup after configured delay (if enabled)
        if self._opts.get("startup_check_enabled", True):
            _delay_ms = max(1, self._opts.get("startup_check_min", 3)) * 60 * 1000
            self.after(_delay_ms, self._test_auth)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._restart_timeout_timer()
        self.after(200, self._startup_log)

    # ── Custom menubar (Frame-based, full colour control on Windows) ──────────
    _MB_BG      = "#1e1e1e"   # menubar bar background
    _MB_FG      = "#ffffff"   # resting label text
    _MB_BTN_BG  = "#1e1e1e"   # resting button bg
    _MB_HOV_BG  = "#0078d4"   # hover / active button bg
    _DD_BG      = "#252526"   # dropdown panel bg
    _DD_FG      = "#ffffff"   # dropdown item text
    _DD_HOV_BG  = "#0078d4"   # dropdown item hover bg
    _DD_DIS_FG  = "#666666"   # disabled item text

    def _build_menu(self):
        self._lock_var    = tk.BooleanVar(value=False)
        self._active_dd   = None   # currently open dropdown frame

        # ── menubar strip ──────────────────────────────────────────────────
        self._menubar = tk.Frame(self, bg=self._MB_BG, height=22)
        self._menubar.pack(fill=tk.X, side=tk.TOP)

        self._tools_btn = tk.Label(
            self._menubar, text="  Tools  ",
            bg=self._MB_BTN_BG, fg=self._MB_FG,
            font=("Segoe UI", 9), cursor="hand2", pady=3)
        self._tools_btn.pack(side=tk.LEFT)
        self._tools_btn.bind("<Button-1>",  self._toggle_tools_menu)
        self._tools_btn.bind("<Enter>",
            lambda e: self._tools_btn.config(bg=self._MB_HOV_BG))
        self._tools_btn.bind("<Leave>",
            lambda e: self._tools_btn.config(
                bg=self._MB_HOV_BG if self._active_dd else self._MB_BTN_BG))

        # dismiss dropdown when clicking anywhere else
        self.bind("<Button-1>", self._dismiss_if_outside, add=True)

    def _make_dropdown(self) -> tk.Frame:
        """Build and return a new dropdown frame positioned below the Tools button."""
        dd = tk.Frame(self, bg=self.__DD_BG if False else self._DD_BG,
                      relief=tk.FLAT, bd=1, highlightthickness=1,
                      highlightbackground="#444444")

        def _item(label, cmd, disabled=False):
            fg  = self._DD_DIS_FG if disabled else self._DD_FG
            lbl = tk.Label(dd, text=label, bg=self._DD_BG, fg=fg,
                           font=("Segoe UI", 9), anchor="w",
                           padx=14, pady=5, cursor="" if disabled else "hand2")
            lbl.pack(fill=tk.X)
            if not disabled:
                lbl.bind("<Enter>",
                    lambda e, w=lbl: w.config(bg=self._DD_HOV_BG, fg="white"))
                lbl.bind("<Leave>",
                    lambda e, w=lbl: w.config(bg=self._DD_BG, fg=self._DD_FG))
                lbl.bind("<Button-1>", lambda e, c=cmd: (self._close_dropdown(), c()))
            return lbl

        def _sep():
            tk.Frame(dd, bg="#444444", height=1).pack(fill=tk.X, padx=6, pady=2)

        locked = self._lock_var.get()
        self._dd_delay_lbl   = _item("Session",      self._open_options,    locked)
        self._dd_close_lbl   = _item("Auto Control", self._open_auto_close, locked)
        _sep()
        self._dd_email_lbl   = _item("Email Alert",  self._open_email_alert, locked)
        return dd

    def _toggle_tools_menu(self, event=None):
        if self._active_dd:
            self._close_dropdown()
            return
        btn = self._tools_btn
        x   = btn.winfo_rootx() - self.winfo_rootx()
        y   = btn.winfo_rooty() - self.winfo_rooty() + btn.winfo_height()
        dd  = self._make_dropdown()
        dd.place(x=x, y=y)
        dd.lift()
        self._active_dd = dd
        self._tools_btn.config(bg=self._MB_HOV_BG)

    def _close_dropdown(self):
        if self._active_dd:
            self._active_dd.destroy()
            self._active_dd = None
        self._tools_btn.config(bg=self._MB_BTN_BG)

    def _dismiss_if_outside(self, event):
        if self._active_dd is None:
            return
        # keep open if click is inside the dropdown or the Tools button
        w = event.widget
        while w:
            if w is self._active_dd or w is self._tools_btn:
                return
            try:
                w = w.master
            except AttributeError:
                break
        self._close_dropdown()

    def _refresh_tools_items(self, locked: bool):
        """Update dropdown item appearance when lock state changes.
        Called from _toggle_lock; dropdown may not be open — that's fine."""
        if self._active_dd is None:
            return
        fg_norm = self._DD_FG
        fg_dis  = self._DD_DIS_FG
        cur     = "" if locked else "hand2"
        for lbl in (self._dd_delay_lbl, self._dd_close_lbl, self._dd_email_lbl):
            try:
                lbl.config(fg=fg_dis if locked else fg_norm, cursor=cur)
            except tk.TclError:
                pass

    # ── Clock section ─────────────────────────────────────────────────────────
    def _build_clock(self, p):
        self._clock_lbl = tk.Label(p, text="", font=("Segoe UI", 22, "bold"),
                                   bg=BG, fg=CYAN)
        self._clock_lbl.pack(pady=(0, 2))

        self._next_lbl = tk.Label(p, text="", font=("Segoe UI", 9),
                                  bg=BG, fg="#888888")
        self._next_lbl.pack(pady=(0, 1))

        self._timeout_lbl = tk.Label(p, text="", font=("Segoe UI", 8),
                                     bg=BG, fg="#666666")
        self._timeout_lbl.pack(pady=(0, 0))

    def _build_cd_label(self, p):
        self._cd_lbl = tk.Label(p, text="", font=("Segoe UI", 11),
                                bg=BG, fg=GOLD, width=38)
        self._cd_lbl.pack()

    # ── Buttons row ───────────────────────────────────────────────────────────
    def _build_exec_buttons(self, p):
        row = tk.Frame(p, bg=BG)
        row.pack(pady=8)

        self._exec_btn = tk.Button(
            row, text="Execute", width=14, height=2,
            font=("Segoe UI", 11, "bold"),
            bg=BLUE, fg="white", activebackground="#155a94",
            relief=tk.FLAT, cursor="hand2", command=self._on_execute)
        self._exec_btn.pack(side=tk.LEFT, padx=(0, 10))

        self._cancel_btn = tk.Button(
            row, text="Cancel", width=10, height=2,
            font=("Segoe UI", 11),
            bg="#6b3030", fg="white", activebackground="#8b2020",
            relief=tk.FLAT, cursor="hand2", state=tk.DISABLED,
            command=self._on_cancel)
        self._cancel_btn.pack(side=tk.LEFT, padx=(0, 10))

        self._lock_btn = tk.Button(
            row, width=6, height=2,
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT, cursor="hand2",
            command=self._toggle_lock)
        self._lock_btn.pack(side=tk.LEFT, padx=(10, 0))
        self._refresh_lock_btn()

        row2 = tk.Frame(p, bg=BG)
        row2.pack(pady=(0, 4))
        self._auth_btn = tk.Button(
            row2, text="🔑 Auth Test", width=16, height=1,
            font=("Segoe UI", 9),
            bg="#3a3a3a", fg=FG, activebackground="#4a4a4a",
            relief=tk.FLAT, cursor="hand2",
            command=self._test_auth)
        self._auth_btn.pack(side=tk.LEFT, padx=(0, 6))

        self._renew_btn = tk.Button(
            row2, text="🔄 Renew Auth", width=16, height=1,
            font=("Segoe UI", 9),
            bg="#3a3a3a", fg=FG, activebackground="#4a4a4a",
            relief=tk.FLAT, cursor="hand2",
            command=self._renew_auth)
        self._renew_btn.pack(side=tk.LEFT)

    # ── Schedule panel ────────────────────────────────────────────────────────
    def _build_schedule(self, p):
        self._spinboxes: list = []

        frame = tk.LabelFrame(p, text=" Set Schedule ",
                              font=("Segoe UI", 9, "bold"),
                              bg=BG, fg=FG, bd=1, relief=tk.GROOVE,
                              padx=8, pady=6)
        frame.pack(fill=tk.X, pady=(4, 4))

        def _make_time_row(label_text, h_val, m_val):
            row = tk.Frame(frame, bg=BG)
            row.pack(anchor=tk.CENTER, pady=(0, 4))
            tk.Label(row, text=label_text, bg=BG, fg=FG,
                     font=("Segoe UI", 9), width=8, anchor=tk.E
                     ).pack(side=tk.LEFT, padx=(0, 4))

            # allow only digits, max 2 chars
            vcmd = (self.register(
                lambda s: s == "" or (s.isdigit() and len(s) <= 2)
            ), "%P")

            hv = tk.StringVar(value=f"{h_val:02d}")
            sbh = tk.Spinbox(row, from_=0, to=23, width=3, textvariable=hv,
                             format="%02.0f", wrap=True,
                             font=("Segoe UI", 11, "bold"), justify=tk.CENTER,
                             bg=BG_PANEL, fg=CYAN, buttonbackground=BG_PANEL,
                             relief=tk.FLAT, highlightthickness=0,
                             validate="key", validatecommand=vcmd,
                             command=self._save)
            sbh.pack(side=tk.LEFT)
            sbh.bind("<FocusOut>", lambda e, v=hv: self._clamp_spinbox(v, 23))
            sbh.bind("<Return>",   lambda e, v=hv: self._clamp_spinbox(v, 23))
            self._spinboxes.append(sbh)

            tk.Label(row, text=":", bg=BG, fg=FG,
                     font=("Segoe UI", 13, "bold")).pack(side=tk.LEFT)

            mv = tk.StringVar(value=f"{m_val:02d}")
            sbm = tk.Spinbox(row, from_=0, to=59, width=3, textvariable=mv,
                             format="%02.0f", wrap=True,
                             font=("Segoe UI", 11, "bold"), justify=tk.CENTER,
                             bg=BG_PANEL, fg=CYAN, buttonbackground=BG_PANEL,
                             relief=tk.FLAT, highlightthickness=0,
                             validate="key", validatecommand=vcmd,
                             command=self._save)
            sbm.pack(side=tk.LEFT)
            sbm.bind("<FocusOut>", lambda e, v=mv: self._clamp_spinbox(v, 59))
            sbm.bind("<Return>",   lambda e, v=mv: self._clamp_spinbox(v, 59))
            self._spinboxes.append(sbm)
            return hv, mv

        self._t1h, self._t1m = _make_time_row(
            "Time 1:", self._sched["t1h"], self._sched["t1m"])
        self._t2h, self._t2m = _make_time_row(
            "Time 2:", self._sched["t2h"], self._sched["t2m"])

        en_row = tk.Frame(frame, bg=BG)
        en_row.pack(anchor=tk.CENTER, pady=(0, 6))
        self._en_var = tk.BooleanVar(value=self._sched.get("enabled", False))
        self._en_btn = tk.Button(en_row, font=("Segoe UI", 9, "bold"),
                                 relief=tk.FLAT, cursor="hand2", width=10,
                                 command=self._toggle_enable)
        self._en_btn.pack()
        self._refresh_en_btn()

        self._cal_frame = tk.Frame(frame, bg=BG)
        self._cal_frame.pack()
        self._build_calendar()

    def _build_calendar(self):
        for w in self._cal_frame.winfo_children():
            w.destroy()
        self._day_btns.clear()

        year, month = self._view_year, self._view_month

        nav = tk.Frame(self._cal_frame, bg=BG)
        nav.grid(row=0, column=0, columnspan=7, sticky="ew", pady=(0, 4))
        tk.Button(nav, text="◀", bg=BG, fg=FG, relief=tk.FLAT,
                  font=("Segoe UI", 10), cursor="hand2",
                  command=self._prev_month).pack(side=tk.LEFT)
        tk.Label(nav, text=datetime.date(year, month, 1).strftime("%B %Y"),
                 bg=BG, fg=CYAN, font=("Segoe UI", 11, "bold"),
                 width=16).pack(side=tk.LEFT, expand=True)
        tk.Button(nav, text="▶", bg=BG, fg=FG, relief=tk.FLAT,
                  font=("Segoe UI", 10), cursor="hand2",
                  command=self._next_month).pack(side=tk.RIGHT)

        for col, name in enumerate(["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]):
            tk.Label(self._cal_frame, text=name, width=5, bg=BG,
                     fg=RED_HDR if col in (0, 6) else FG,
                     font=("Segoe UI", 9, "bold")).grid(row=1, column=col, padx=1)

        today = datetime.date.today()
        weeks = _cal.Calendar(firstweekday=6).monthdayscalendar(year, month)
        for cal_row, week in enumerate(weeks, start=2):
            for col, day_num in enumerate(week):
                if day_num == 0:
                    tk.Label(self._cal_frame, text="", width=5,
                             bg=BG).grid(row=cal_row, column=col, padx=1, pady=1)
                    continue
                d       = datetime.date(year, month, day_num)
                key     = d.isoformat()
                is_wkend = col in (0, 6)
                is_past  = d < today
                is_today = d == today
                on       = self._is_day_on(d)
                if is_past:
                    btn = tk.Button(self._cal_frame, text=str(day_num), width=4,
                                    font=("Segoe UI", 9), relief=tk.FLAT,
                                    cursor="", fg=FG_PAST, bg=GREY_PAST,
                                    activebackground=GREY_PAST, state=tk.DISABLED,
                                    disabledforeground=FG_PAST)
                else:
                    btn = tk.Button(self._cal_frame, text=str(day_num), width=4,
                                    font=("Segoe UI", 9, "bold"), relief=tk.FLAT,
                                    cursor="hand2", fg=FG_NUM,
                                    disabledforeground=FG_NUM,
                                    highlightthickness=2 if is_today else 0,
                                    highlightbackground=TODAY_BDR,
                                    command=lambda k=key: self._toggle_day(k))
                btn.grid(row=cal_row, column=col, padx=1, pady=1)
                if not is_past:
                    self._day_btns[key] = btn
                    self._refresh_day_btn(key, is_wkend, on)

    def _is_day_on(self, d: datetime.date) -> bool:
        key = d.isoformat()
        return self._overrides.get(key, _default_on(d))

    def _refresh_day_btn(self, key: str, is_wkend: bool, on: bool):
        btn = self._day_btns.get(key)
        if btn is None:
            return
        if on:
            btn.config(bg=RED_ON if is_wkend else GREEN_ON,
                       activebackground=RED_ON if is_wkend else GREEN_ON)
        else:
            btn.config(bg=GREY_OFF, activebackground="#4a4a4a")

    def _toggle_day(self, key: str):
        if self._lock_var.get():
            return
        d = datetime.date.fromisoformat(key)
        if d < datetime.date.today():
            return
        new_val = not self._is_day_on(d)
        if new_val == _default_on(d):
            self._overrides.pop(key, None)
        else:
            self._overrides[key] = new_val
        self._refresh_day_btn(key, d.weekday() >= 5, new_val)
        self._save()

    def _prev_month(self):
        if self._view_month == 1:
            self._view_year -= 1; self._view_month = 12
        else:
            self._view_month -= 1
        self._build_calendar()

    def _next_month(self):
        if self._view_month == 12:
            self._view_year += 1; self._view_month = 1
        else:
            self._view_month += 1
        self._build_calendar()

    def _clamp_spinbox(self, var: tk.StringVar, max_val: int):
        """Clamp, zero-pad and save when a spinbox loses focus or Enter is pressed."""
        try:
            v = max(0, min(max_val, int(var.get())))
        except ValueError:
            v = 0
        var.set(f"{v:02d}")
        self._save()

    def _toggle_enable(self):
        self._en_var.set(not self._en_var.get())
        self._refresh_en_btn()
        self._save()

    def _refresh_en_btn(self):
        on = self._en_var.get()
        self._en_btn.config(text="● ON" if on else "○ OFF",
                            bg=GREEN if on else "#555555", fg="white",
                            activebackground=GREEN if on else "#666666")

    # ── Log panel ─────────────────────────────────────────────────────────────
    def _build_log(self, p):
        log_row = tk.Frame(p, bg=BG)
        log_row.pack(anchor=tk.W, pady=(4, 0))
        self._log_file_var = tk.BooleanVar(value=self._opts.get("log_enabled", True))
        self._log_file_btn = tk.Button(log_row, font=("Segoe UI", 8, "bold"),
                                       relief=tk.FLAT, cursor="hand2", width=5,
                                       command=self._toggle_log_file)
        self._log_file_btn.pack(side=tk.LEFT, padx=(0, 5))
        self._refresh_log_file_btn()
        tk.Label(log_row, text="Save log to log.log",
                 bg=BG, fg=FG, font=("Segoe UI", 8)).pack(side=tk.LEFT)

        self._log_box = scrolledtext.ScrolledText(
            p, width=54, height=10, state=tk.DISABLED,
            font=("Consolas", 9), bg=BG_PANEL, fg=FG,
            relief=tk.FLAT, insertbackground="white")
        self._log_box.pack(pady=(2, 0))

    def _toggle_log_file(self):
        self._log_file_var.set(not self._log_file_var.get())
        self._refresh_log_file_btn()
        self._opts["log_enabled"] = self._log_file_var.get()
        _save_opts(self._opts)

    def _refresh_log_file_btn(self):
        on = self._log_file_var.get()
        self._log_file_btn.config(text="● ON" if on else "○ OFF",
                                  bg=GREEN if on else "#555555", fg="white",
                                  activebackground=GREEN if on else "#666666")

    # ── Clock tick (1 s) ──────────────────────────────────────────────────────
    def _tick_clock(self):
        self._clock_lbl.config(text=time.strftime("%H:%M"))
        self._refresh_next_lbl()
        # align next tick to the start of the next minute
        now = datetime.datetime.now()
        ms_until_next_min = (60 - now.second) * 1000 - now.microsecond // 1000
        self.after(max(ms_until_next_min, 500), self._tick_clock)

    def _refresh_sess_label(self, force: bool = False):
        pass  # session label removed

    def _cookie_expiry(self, force: bool = False) -> datetime.date | None:
        """Return the expiry date of the persistent login cookie.
        Result is cached per calendar day; pass force=True to re-read from disk.
        Ignores short-lived session cookies (lifetime < 30 days).
        Falls back to SESSION_TS + SESSION_DAYS if the JSON is absent or unreadable."""
        today = datetime.date.today()
        if not force and self._expiry_cache_day == today:
            return self._expiry_cache
        result = None
        if os.path.exists(SESSION_FILE):
            try:
                data = json.loads(open(SESSION_FILE, encoding="utf-8").read())
                today_ts = datetime.datetime.now().timestamp()
                # only consider cookies that live at least 30 days from now
                stamps = [
                    c["expires"]
                    for c in data.get("cookies", [])
                    if c.get("expires", -1) - today_ts >= 30 * 86400
                ]
                if stamps:
                    result = datetime.date.fromtimestamp(min(stamps))
            except Exception:
                pass
        if result is None:
            # fallback: SESSION_TS file
            if os.path.exists(SESSION_TS):
                try:
                    saved = datetime.date.fromisoformat(open(SESSION_TS).read().strip())
                    result = saved + datetime.timedelta(days=SESSION_DAYS)
                except Exception:
                    pass
            if result is None and os.path.exists(SESSION_FILE):
                saved = datetime.date.fromtimestamp(os.path.getmtime(SESSION_FILE))
                result = saved + datetime.timedelta(days=SESSION_DAYS)
        self._expiry_cache     = result
        self._expiry_cache_day = today
        return result

    # ── Next-fire label ───────────────────────────────────────────────────────
    def _read_slots(self) -> list[tuple[int, int]] | None:
        """Read and clamp spinbox values. Returns None if unparseable."""
        try:
            return [
                (max(0, min(23, int(self._t1h.get()))),
                 max(0, min(59, int(self._t1m.get())))),
                (max(0, min(23, int(self._t2h.get()))),
                 max(0, min(59, int(self._t2m.get())))),
            ]
        except ValueError:
            return None

    def _next_fire_dt(self) -> datetime.datetime | None:
        """Find next scheduled slot (up to 7 days ahead)."""
        if not self._en_var.get():
            return None
        slots = self._read_slots()
        if slots is None:
            return None
        now = datetime.datetime.now()
        for days_ahead in range(8):
            d = now.date() + datetime.timedelta(days=days_ahead)
            if not self._is_day_on(d):
                continue
            for h, m in sorted(slots):          # earliest slot first
                dt = datetime.datetime(d.year, d.month, d.day, h, m)
                if dt > now:
                    return dt
        return None

    def _eta_str(self, target: datetime.datetime) -> str:
        total_s = max(0, int((target - datetime.datetime.now()).total_seconds()))
        hh, mm  = divmod(total_s // 60, 60)
        if hh:   return f"{hh}h {mm}m"
        elif mm: return f"{mm}m"
        else:    return "< 1 min"

    def _delay_label(self) -> str:
        """Human-readable description of the configured delay."""
        if self._opts.get("rand_enabled", True):
            lo = self._opts.get("rand_min_min", 1)
            hi = self._opts.get("rand_max_min", 10)
            return f"+{lo}–{hi} min + 0–59 s"
        return "+0–59 s"

    def _next_fire_str(self) -> str:
        nxt = self._next_fire_dt()
        if nxt is None:
            return ""
        icon = "🔒" if self._lock_var.get() else "🔄"
        return (f"{icon} Next trigger: {nxt.strftime('%a %H:%M')}"
                f"  (in {self._eta_str(nxt)})  [{self._delay_label()}]")

    def _refresh_next_lbl(self):
        txt = self._next_fire_str()
        if txt:
            self._next_lbl.config(text=txt, fg=GOLD)
        else:
            self._next_lbl.config(text="Schedule OFF", fg="#555555")

    # ── Unified scheduler (unlocked=30 s, locked=minute-aligned) ──────────────
    def _tick_schedule(self):
        """Checks datetime.now() against slots; interval adapts to lock state."""
        self._poll_id = None
        today = datetime.date.today()
        # purge yesterday's fired / pre-check keys
        self._fired_keys       = {k for k in self._fired_keys
                                  if k.startswith(today.isoformat())}
        self._pre_checked_keys = {k for k in self._pre_checked_keys
                                  if k.startswith(today.isoformat())}
        # auto-renew session once per day if expiry is close
        if (self._opts.get("auto_renew_enabled", True)
                and self._auto_renew_date != today):
            threshold = self._opts.get("auto_renew_days", 7)
            expiry = self._cookie_expiry()
            if expiry is not None:
                remaining = (expiry - today).days
                if 0 <= remaining <= threshold:
                    self._auto_renew_date = today
                    self._log_msg(
                        f"[AutoRenew] Session expires in {remaining}d — renewing now …")
                    threading.Thread(target=self._auto_renew_session,
                                     daemon=True).start()
        if self._en_var.get() and self._is_day_on(today):
            now = datetime.datetime.now()
            slots = self._read_slots() or [
                (self._sched["t1h"], self._sched["t1m"]),
                (self._sched["t2h"], self._sched["t2m"]),
            ]
            # ── pre-fire session check (1 h before each slot) ──────────────
            for h, m in slots:
                slot_dt  = datetime.datetime(today.year, today.month, today.day, h, m)
                slot_key = f"{today.isoformat()} {h:02d}:{m:02d}"
                mins_to  = (slot_dt - now).total_seconds() / 60
                if 0 < mins_to <= 60 and slot_key not in self._pre_checked_keys:
                    self._pre_checked_keys.add(slot_key)
                    self._bg_session_check(f"{h:02d}:{m:02d}")
            key_min = f"{today.isoformat()} {now.hour:02d}:{now.minute:02d}"
            if key_min not in self._fired_keys:
                for h, m in slots:
                    if now.hour == h and now.minute == m:
                        if self._exec_btn["state"] == tk.NORMAL:
                            self._fired_keys.add(key_min)
                            self._do_fire()
                        break
        if self._lock_var.get():
            # align to next :00 boundary (same cadence as clock display)
            now = datetime.datetime.now()
            ms_next = max((60 - now.second) * 1000 - now.microsecond // 1000, 500)
            self._poll_id = self.after(ms_next, self._tick_schedule)
        else:
            self._poll_id = self.after(POLL_INTERVAL_MS, self._tick_schedule)

    # ── Shared fire logic ─────────────────────────────────────────────────────
    def _do_fire(self, delay: int | None = None):
        if delay is None:
            delay = _calc_delay(self._opts)
        dm, ds  = divmod(delay, 60)
        exec_dt = datetime.datetime.now() + datetime.timedelta(seconds=delay)
        self._log_msg(
            f"Schedule triggered at {time.strftime('%H:%M')} — "
            f"delay {dm}m {ds:02d}s → exec at {exec_dt.strftime('%H:%M:%S')}.")
        self._on_execute(pre_delay=delay, scheduled=True)

    # ── Logging ───────────────────────────────────────────────────────────────
    def _log_msg(self, msg: str):
        if self._log_file_var.get():
            _write_log(msg)
        def _append():
            self._log_box.config(state=tk.NORMAL)
            self._log_box.insert(tk.END, msg + "\n")
            self._log_box.see(tk.END)
            self._log_box.config(state=tk.DISABLED)
        self.after(0, _append)

    def _set_cd(self, msg: str):
        self.after(0, lambda: self._cd_lbl.config(text=msg))

    # ── Execute / countdown ───────────────────────────────────────────────────
    def _on_execute(self, pre_delay: int | None = None, scheduled: bool = False):
        self._exec_btn.config(state=tk.DISABLED)
        self._cancel_btn.config(state=tk.NORMAL)
        self._cancel_flag.clear()
        self._stop_flag.clear()
        self._close_secs: int | None = None
        n = pre_delay if pre_delay is not None else PRE_DELAY_MANUAL
        self._exec_target_dt = datetime.datetime.now() + datetime.timedelta(seconds=n)
        self._log_msg("─" * 42)
        if scheduled:
            m, s = divmod(n, 60)
            self._log_msg(f"Waiting {m}m {s:02d}s — press Cancel to abort.")
        else:
            self._log_msg(f"Starting in {n} s — press Cancel to abort.")
        self._run_countdown()

    def _run_countdown(self):
        if self._cancel_flag.is_set():
            self._cd_lbl.config(text="Cancelled.")
            self._reset_btns()
            return
        remaining = max(0, int(
            (self._exec_target_dt - datetime.datetime.now()).total_seconds()))
        if remaining <= 0:
            self._cd_lbl.config(text="Running automation …")
            self._launch()
            return
        if self._lock_var.get():
            # minute-resolution, aligned to next :00 boundary (same as clock)
            m = max(1, (remaining + 59) // 60)
            self._cd_lbl.config(
                text=f"Executing in {m} min …" if m > 1 else "Executing in < 1 min …")
            now = datetime.datetime.now()
            ms_next_min = max(
                (60 - now.second) * 1000 - now.microsecond // 1000, 500)
            self._countdown_id = self.after(ms_next_min, self._run_countdown)
        else:
            m, s = divmod(remaining, 60)
            self._cd_lbl.config(
                text=f"Executing in {f'{m}m {s:02d}s' if m else f'{s} s'} …")
            self._countdown_id = self.after(1000, self._run_countdown)

    def _launch(self):
        fire_time = time.strftime("%H:%M:%S")
        self._log_msg(f"[Fire] Clock-in fired at {fire_time}.")
        # determine browser close delay from opts
        if self._opts.get("browser_close_enabled", True):
            close_secs = max(1, min(180, self._opts.get("browser_close_secs", 10)))
            self._log_msg(f"[Fire] Browser auto-close: {close_secs}s.")
        else:
            close_secs = self._close_secs  # falls back to random CLOSE_MIN
            lo, hi = CLOSE_MIN[0] // 60, CLOSE_MIN[1] // 60
            self._log_msg(f"[Fire] Browser auto-close: disabled — random {lo}–{hi} min fallback.")
        def worker():
            def _on_success():
                self._log_msg("[Mission] SUCCESS — clock-in completed.")
                if self._opts.get("close_on_success", False):
                    secs = max(3, min(180, self._opts.get("close_on_success_secs", 5)))
                    self._log_msg(f"[AutoClose] Closing program in {secs}s …")
                    self.after(secs * 1000, self._on_close)
            ok = clock_in(self._log_msg, self._set_cd, self._stop_flag,
                          close_secs,
                          on_success=_on_success,
                          tfa_timeout_ms=max(1, self._opts.get("2fa_timeout_secs", 8)) * 1000)
            if not ok:
                self._log_msg("[Mission] FAILED — see error above.")
            self.after(0, self._reset_btns)
        threading.Thread(target=worker, daemon=True).start()

    def _on_cancel(self):
        self._cancel_flag.set()
        self._stop_flag.set()
        if self._countdown_id:
            self.after_cancel(self._countdown_id)
            self._countdown_id = None
        self._cd_lbl.config(text="Cancelled.")
        self._log_msg("Cancelled by user.")
        self._reset_btns()

    def _reset_btns(self):
        self._exec_btn.config(state=tk.NORMAL)
        self._cancel_btn.config(state=tk.DISABLED)
        self._cd_lbl.config(text="")

    # ── Auto-renew (headless, background) ──────────────────────────────────────
    def _auto_renew_session(self):
        """Headless session refresh — only works while session is still valid."""
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, channel="chrome")
                if not os.path.exists(SESSION_FILE):
                    self._log_msg("[AutoRenew] No session file — skipped.")
                    browser.close()
                    return
                ctx  = browser.new_context(storage_state=SESSION_FILE)
                page = ctx.new_page()
                page.goto(URL_BASE,
                          wait_until="domcontentloaded", timeout=20_000)
                if page.locator(SEL_EXPIRED).is_visible():
                    browser.close()
                    self._log_msg(
                        "[AutoRenew] Session already expired — use 🔄 Renew Auth manually.")
                    return
                ctx.storage_state(path=SESSION_FILE)
                with open(SESSION_TS, "w") as _f:
                    _f.write(datetime.date.today().isoformat())
                browser.close()
            self._log_msg("[AutoRenew] Session renewed successfully.")
            self.after(0, lambda: self._refresh_sess_label(force=True))
        except Exception as exc:
            self._log_msg(f"[AutoRenew] ERROR: {exc}")

    # ── Auth renew ────────────────────────────────────────────────────────────
    def _renew_auth(self):
        """Open a visible browser, refresh (or re-establish) the session, save it."""
        self._renew_btn.config(state=tk.DISABLED, text="Opening…", fg=GOLD)
        self._log_msg("[Renew] Opening Chrome for session renewal …")
        def worker():
            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=False, channel="chrome")
                    if os.path.exists(SESSION_FILE):
                        ctx = browser.new_context(storage_state=SESSION_FILE)
                        self._log_msg("[Renew] Loaded existing session.")
                    else:
                        ctx = browser.new_context()
                        self._log_msg("[Renew] No existing session — log in manually.")
                    page = ctx.new_page()
                    page.goto(URL_BASE,
                              wait_until="domcontentloaded", timeout=20_000)
                    if page.locator(SEL_EXPIRED).is_visible():
                        self._log_msg("[Renew] Session expired — attempting auto-login …")
                        page.fill("#email", EMAIL)
                        if PASSWORD:
                            page.fill("#password", PASSWORD)
                            self._log_msg("[Renew] Credentials filled. Submitting …")
                            page.locator(
                                "button[type='submit'], input[type='submit']"
                            ).first.click()
                            try:
                                page.wait_for_selector(SEL_CLOCKIN, timeout=max(1, self._opts.get("2fa_timeout_secs", 8)) * 1000)
                                self._log_msg("[Renew] Login succeeded (no 2FA).")
                            except Exception:
                                self._log_msg("[Renew] 2FA required — complete it in the browser.")
                                self._log_msg("[Renew] (Check 'Remember me 30 days' then submit 2FA.)")
                                page.wait_for_selector(SEL_CLOCKIN, timeout=0)
                                self._log_msg("[Renew] 2FA complete.")
                        else:
                            page.focus("#password")
                            self._log_msg("[Renew] No password in user.env — enter manually.")
                            page.wait_for_selector(SEL_CLOCKIN, timeout=0)
                            self._log_msg("[Renew] Logged in successfully.")
                    else:
                        self._log_msg("[Renew] Already logged in — saving fresh session.")
                    ctx.storage_state(path=SESSION_FILE)
                    with open(SESSION_TS, "w") as _f:
                        _f.write(datetime.date.today().isoformat())
                    browser.close()
                self._log_msg("[Renew] Session saved.")
                self.after(0, self._renew_done)
            except Exception as exc:
                self._log_msg(f"[Renew] ERROR: {exc}")
                self.after(0, lambda: self._renew_done(error=True))
        threading.Thread(target=worker, daemon=True).start()

    def _renew_done(self, error=False):
        self._renew_btn.config(
            text="❌ Renew failed" if error else "✅ Renewed",
            fg="white",
            bg="#5a2020" if error else GREEN_ON,
            state=tk.NORMAL)
        if not error:
            self._refresh_sess_label(force=True)
        self.after(5_000, lambda: self._renew_btn.config(
            text="🔄 Renew Auth", fg=FG, bg="#3a3a3a"))

    # ── Auth test ─────────────────────────────────────────────────────────────
    def _test_auth(self):
        self._auth_btn.config(state=tk.DISABLED, text="Testing…", fg=GOLD)
        def worker():
            try:
                with sync_playwright() as p:
                    if not os.path.exists(SESSION_FILE):
                        self._log_msg("[Auth] No session file — attempting login …")
                        ok = self._create_session(p)
                        self.after(0, lambda: self._auth_done(False if ok else None))
                        return
                    browser = p.chromium.launch(headless=True, channel="chrome")
                    ctx = browser.new_context(storage_state=SESSION_FILE)
                    self._log_msg("[Auth] Checking session …")
                    page = ctx.new_page()
                    page.goto(URL_BASE,
                              wait_until="domcontentloaded", timeout=20_000)
                    expired = page.locator(SEL_EXPIRED).is_visible()
                    browser.close()
                self.after(0, lambda: self._auth_done(expired))
            except Exception as exc:
                self._log_msg(f"[Auth] ERROR: {exc}")
                self.after(0, lambda: self._auth_done(None))
        threading.Thread(target=worker, daemon=True).start()

    def _create_session(self, pw) -> bool:
        """Attempt to log in and save a session.
        Tries headless first with auto-submit; if 2FA is required opens a visible
        browser and waits for the user to complete it.
        Returns True on success, False on failure."""
        try:
            # ── Phase 1: headless auto-submit ──────────────────────────────
            self._log_msg("[Auth] Trying headless login …")
            browser = pw.chromium.launch(headless=True, channel="chrome")
            ctx  = browser.new_context()
            page = ctx.new_page()
            page.goto(URL_BASE,
                      wait_until="domcontentloaded", timeout=20_000)
            if not page.locator(SEL_EXPIRED).is_visible():
                # Somehow already logged in (unlikely with fresh context)
                ctx.storage_state(path=SESSION_FILE)
                with open(SESSION_TS, "w") as _f:
                    _f.write(datetime.date.today().isoformat())
                browser.close()
                self._log_msg("[Auth] Session saved (no login required).")
                return True
            page.fill("#email", EMAIL)
            page.fill("#password", PASSWORD)
            # submit the form
            page.locator("button[type='submit'], input[type='submit']").first.click()
            try:
                # wait up to 8 s to reach the dashboard
                page.wait_for_selector(SEL_CLOCKIN, timeout=max(1, self._opts.get("2fa_timeout_secs", 8)) * 1000)
                ctx.storage_state(path=SESSION_FILE)
                with open(SESSION_TS, "w") as _f:
                    _f.write(datetime.date.today().isoformat())
                browser.close()
                self._log_msg("[Auth] Login succeeded (no 2FA). Session saved.")
                return True
            except Exception:
                browser.close()
                self._log_msg("[Auth] 2FA required — opening browser …")
            # ── Phase 2: visible browser for 2FA ──────────────────────────
            browser = pw.chromium.launch(headless=False, channel="chrome")
            ctx  = browser.new_context()
            page = ctx.new_page()
            page.goto(URL_BASE,
                      wait_until="domcontentloaded", timeout=20_000)
            page.fill("#email", EMAIL)
            page.fill("#password", PASSWORD)
            page.locator("button[type='submit'], input[type='submit']").first.click()
            self._log_msg("[Auth] Complete 2FA in the browser window …")
            page.wait_for_selector(SEL_CLOCKIN, timeout=0)
            ctx.storage_state(path=SESSION_FILE)
            with open(SESSION_TS, "w") as _f:
                _f.write(datetime.date.today().isoformat())
            browser.close()
            self._log_msg("[Auth] 2FA complete. Session saved.")
            return True
        except Exception as exc:
            self._log_msg(f"[Auth] Login failed: {exc}")
            return False

    def _auth_done(self, expired: bool | None):
        if expired is None:
            self._auth_btn.config(text="🔑 Auth Test", fg=FG,
                                  bg="#5a2020", state=tk.NORMAL)
            self._log_msg("[Auth] Could not reach CMS.")
        elif expired:
            self._auth_btn.config(text="❌ Session Expired", fg="white",
                                  bg=RED_ON, state=tk.NORMAL)
            self._log_msg("[Auth] Session EXPIRED — auto-renewing …")
            self.after(500, self._renew_auth)
            return
        else:
            self._auth_btn.config(text="✅ Session Valid", fg="white",
                                  bg=GREEN_ON, state=tk.NORMAL)
            self._log_msg("[Auth] Session is valid.")
        # reset label after 5 s
        self.after(5_000, lambda: self._auth_btn.config(
            text="🔑 Auth Test", fg=FG, bg="#3a3a3a"))

    # ── Lock ─────────────────────────────────────────────────────────────────
    def _toggle_lock(self):
        self._lock_var.set(not self._lock_var.get())
        locked = self._lock_var.get()
        state  = tk.DISABLED if locked else tk.NORMAL
        for w in self._spinboxes:
            w.config(state=state)
        self._en_btn.config(state=state)
        self._log_file_btn.config(state=state)
        for btn in self._day_btns.values():
            btn.config(state=state, cursor="" if locked else "hand2")
        self._refresh_tools_items(locked)
        self._refresh_lock_btn()
        self.title(f"{'🔒 ' if locked else ''}{_APP_NAME}  v{_APP_VERSION}")
        # persist lock state
        self._sched["locked"] = locked
        _save_sched(self._sched)
        # restart scheduler — it self-selects the right interval
        if self._poll_id:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        self._tick_schedule()

    def _refresh_lock_btn(self):
        locked = self._lock_var.get()
        self._lock_btn.config(
            text="🔒" if locked else "🔓",
            bg="#7a5500" if locked else "#3a3a3a",
            fg=GOLD if locked else FG,
            activebackground="#9a6a00" if locked else "#4a4a4a")

    # ── Save ──────────────────────────────────────────────────────────────────
    def _save(self):
        if self._lock_var.get():
            return
        try:
            t1h = max(0, min(23, int(self._t1h.get())))
            t1m = max(0, min(59, int(self._t1m.get())))
            t2h = max(0, min(23, int(self._t2h.get())))
            t2m = max(0, min(59, int(self._t2m.get())))
        except ValueError:
            return
        self._sched = {"t1h": t1h, "t1m": t1m, "t2h": t2h, "t2m": t2m,
                       "enabled": bool(self._en_var.get()),
                       "locked": bool(self._lock_var.get()),
                       "overrides": dict(self._overrides)}
        _save_sched(self._sched)
        # prune pre-check keys that no longer match the current slots
        today_iso = datetime.date.today().isoformat()
        valid_slot_keys = {
            f"{today_iso} {t1h:02d}:{t1m:02d}",
            f"{today_iso} {t2h:02d}:{t2m:02d}",
        }
        self._pre_checked_keys &= valid_slot_keys

    # ── Options dialog ────────────────────────────────────────────────────────
    def _open_options(self):
        _OptionsDialog(self, self._opts, self._on_opts_saved)

    def _on_opts_saved(self, opts: dict):
        self._opts = opts
        _save_opts(opts)
        o = opts
        self._log_msg(
            f"[Options] Saved: "
            f"rand={'on' if o.get('rand_enabled') else 'off'}"
            f"({o.get('rand_min_min')}-{o.get('rand_max_min')}min)  "
            f"renew={'on' if o.get('auto_renew_enabled') else 'off'}({o.get('auto_renew_days')}d)  "
            f"startupCheck={'on' if o.get('startup_check_enabled') else 'off'}({o.get('startup_check_min')}min)  "
            f"browserClose={'on' if o.get('browser_close_enabled') else 'off'}({o.get('browser_close_secs')}s)  "
            f"closeOnSuccess={'on' if o.get('close_on_success') else 'off'}  "
            f"logging={'on' if o.get('log_enabled') else 'off'}")
        # sync log toggle button with saved preference
        self._log_file_var.set(opts.get("log_enabled", True))
        self._refresh_log_file_btn()
        self._restart_timeout_timer()

    def _restart_timeout_timer(self):
        """Cancel any existing idle-timeout timer and start a fresh one if enabled."""
        if self._auto_close_timer_id:
            self.after_cancel(self._auto_close_timer_id)
            self._auto_close_timer_id = None
        if self._timeout_tick_id:
            self.after_cancel(self._timeout_tick_id)
            self._timeout_tick_id = None
        self._timeout_deadline = None
        if self._opts.get("auto_close_timeout_enabled", False):
            minutes = max(5, min(60, self._opts.get("auto_close_timeout_min", 15)))
            ms = minutes * 60 * 1000
            self._timeout_deadline = datetime.datetime.now() + datetime.timedelta(minutes=minutes)
            self._auto_close_timer_id = self.after(ms, self._timeout_close)
            self._log_msg(f"[Timeout] Auto-close timer set — {minutes} min.")
            self._tick_timeout_label()
        else:
            self._timeout_lbl.config(text="")

    def _tick_timeout_label(self):
        if self._timeout_deadline is None:
            self._timeout_lbl.config(text="")
            return
        remaining = max(0, int(
            (self._timeout_deadline - datetime.datetime.now()).total_seconds()))
        m, s = divmod(remaining, 60)
        self._timeout_lbl.config(text=f"Auto-close in {m}m {s:02d}s")
        if remaining > 0:
            self._timeout_tick_id = self.after(1000, self._tick_timeout_label)
        else:
            self._timeout_tick_id = None

    def _timeout_close(self):
        self._auto_close_timer_id = None
        self._timeout_deadline = None
        if self._timeout_tick_id:
            self.after_cancel(self._timeout_tick_id)
            self._timeout_tick_id = None
        self._timeout_lbl.config(text="")
        self._log_msg("[Timeout] Idle timeout reached — closing program.")
        self._on_close()

    def _open_auto_close(self):
        _AutoCloseDialog(self, self._opts, self._on_opts_saved)

    def _open_email_alert(self):
        _EmailAlertDialog(self, self._log_msg)

    def _startup_log(self):
        self._log_msg(f"[Startup] {_APP_NAME} v{_APP_VERSION} started at "
                      f"{time.strftime('%Y-%m-%d %H:%M:%S')}.")
        self._log_msg(f"[Startup] Data dir: {HERE}")
        if os.path.exists(SESSION_FILE):
            expiry = self._cookie_expiry()
            if expiry:
                days_left = (expiry - datetime.date.today()).days
                self._log_msg(f"[Session] File found — expires {expiry.isoformat()} "
                              f"({days_left}d remaining).")
            else:
                self._log_msg("[Session] File found — expiry unknown.")
        else:
            self._log_msg("[Session] No session file — login required.")
        # schedule summary
        slots = self._read_slots()
        if slots:
            t1h, t1m = slots[0]
            t2h, t2m = slots[1]
            self._log_msg(
                f"[Startup] Schedule: T1={t1h:02d}:{t1m:02d}  T2={t2h:02d}:{t2m:02d}  "
                f"enabled={'yes' if self._en_var.get() else 'no'}  "
                f"locked={'yes' if self._lock_var.get() else 'no'}")
        # options summary
        o = self._opts
        self._log_msg(
            f"[Startup] Opts: "
            f"rand={'on' if o.get('rand_enabled') else 'off'}"
            f"({o.get('rand_min_min')}-{o.get('rand_max_min')}min)  "
            f"renew={'on' if o.get('auto_renew_enabled') else 'off'}({o.get('auto_renew_days')}d)  "
            f"startupCheck={'on' if o.get('startup_check_enabled') else 'off'}({o.get('startup_check_min')}min)  "
            f"browserClose={'on' if o.get('browser_close_enabled') else 'off'}({o.get('browser_close_secs')}s)  "
            f"closeOnSuccess={'on' if o.get('close_on_success') else 'off'}  "
            f"logging={'on' if o.get('log_enabled') else 'off'}")
        # SMTP status
        if SMTP_CFG["host"] and SMTP_CFG["user"] and SMTP_CFG["password"] and SMTP_CFG["to"]:
            self._log_msg(
                f"[Startup] SMTP: configured — {len(SMTP_CFG['to'])} recipient(s): "
                f"{', '.join(SMTP_CFG['to'])}.")
        else:
            self._log_msg("[Startup] SMTP: not configured — email alerts disabled.")

    def _bg_session_check(self, slot_label: str):
        """Headless session validity check run 1 h before a scheduled slot."""
        self._log_msg(f"[PreCheck] Checking session 1 h before {slot_label} …")
        def worker():
            try:
                with sync_playwright() as p:
                    if not os.path.exists(SESSION_FILE):
                        self.after(0, lambda: self._log_msg(
                            "[PreCheck] No session file — skipped."))
                        return
                    browser = p.chromium.launch(headless=True, channel="chrome")
                    ctx  = browser.new_context(storage_state=SESSION_FILE)
                    page = ctx.new_page()
                    page.goto(URL_BASE,
                              wait_until="domcontentloaded", timeout=20_000)
                    # Wait for either the login form or the clock-in button to
                    # fully render before checking — avoids SPA race conditions.
                    try:
                        page.wait_for_selector(SEL_PRECHECK, timeout=10_000)
                    except Exception:
                        pass
                    expired = page.locator(SEL_EXPIRED).is_visible()
                    browser.close()
                if expired:
                    self.after(0, lambda: self._log_msg(
                        f"[PreCheck] ⚠ Session EXPIRED before {slot_label} "
                        "— use 🔄 Renew Auth now!"))
                    # send email alert in the same background thread
                    sent = _send_alert_email(
                        subject=f"[AutoID] ⚠ Session expired — {slot_label} at risk",
                        body=(
                            f"AutoID pre-check alert\n"
                            f"Scheduled slot : {slot_label}\n"
                            f"Checked at     : {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                            "The CMS session has EXPIRED.\n"
                            "Please open the program and use the Renew Auth button "
                            "before the scheduled clock-in fires.\n"
                        ),
                    )
                    if sent:
                        self.after(0, lambda: self._log_msg(
                            "[PreCheck] Alert email sent."))
                    elif all([SMTP_CFG["host"], SMTP_CFG["user"],
                              SMTP_CFG["password"]]) and SMTP_CFG["to"]:
                        self.after(0, lambda: self._log_msg(
                            "[PreCheck] Alert email FAILED to send."))
                    else:
                        self.after(0, lambda: self._log_msg(
                            "[PreCheck] Email not configured in user.env — skipped."))
                else:
                    self.after(0, lambda: self._log_msg(
                        f"[PreCheck] Session valid for {slot_label}."))
            except Exception as exc:
                self.after(0, lambda e=exc: self._log_msg(f"[PreCheck] ERROR: {e}"))
        threading.Thread(target=worker, daemon=True).start()

    def _on_close(self):
        self._log_msg(f"[Shutdown] Program closed at {time.strftime('%Y-%m-%d %H:%M:%S')}.")
        self.after(200, self.destroy)


# ── Options dialog ─────────────────────────────────────────────────────────────
class _OptionsDialog(tk.Toplevel):
    """Tools → Options: random delay settings."""

    def __init__(self, parent, opts: dict, on_save):
        super().__init__(parent)
        self._on_save = on_save
        self._opts    = dict(opts)
        self.title("Options — Random Delay")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.wm_attributes("-topmost", True)
        self.grab_set()
        if os.path.exists(_ICO_FILE):
            try:
                self.iconbitmap(_ICO_FILE)
            except Exception:
                pass
        self._build_ui()
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(
            f"+{(sw - self.winfo_width()) // 2}+{(sh - self.winfo_height()) // 2}")

    def _build_ui(self):
        FH = ("Segoe UI", 9, "bold")
        FN = ("Segoe UI", 10)
        FNB = ("Segoe UI", 10, "bold")
        PAD = {"padx": 16, "pady": 6}

        # ── Startup Session Check (top) ────────────────────────────────────
        tk.Label(self, text="Startup Session Check",
                 bg=BG, fg=CYAN, font=("Segoe UI", 11, "bold")
                 ).grid(row=0, column=0, columnspan=2, pady=(14, 4), padx=16, sticky="w")
        self._sc_en_var = tk.BooleanVar(
            value=self._opts.get("startup_check_enabled", True))
        self._sc_en_btn = tk.Button(self, font=FH, relief=tk.FLAT,
                                    cursor="hand2", width=8,
                                    command=self._toggle_sc_en)
        self._sc_en_btn.grid(row=0, column=2, sticky="w", padx=(0, 16), pady=(14, 4))
        self._refresh_sc_en_btn()

        tk.Label(self, text="Check delay after startup (min):", bg=BG, fg=FG,
                 font=FN).grid(row=1, column=0, sticky="w", **PAD)
        self._sc_var = tk.StringVar(
            value=str(self._opts.get("startup_check_min", 3)))
        self._sc_sb = tk.Spinbox(
            self, from_=1, to=10, width=5, textvariable=self._sc_var,
            wrap=False, font=FNB, justify=tk.CENTER,
            bg=BG_PANEL, fg=GOLD, buttonbackground=BG_PANEL,
            relief=tk.FLAT, highlightthickness=0)
        self._sc_sb.grid(row=1, column=1, sticky="w", padx=(0, 16), pady=6)
        tk.Label(self, text="Range: 1 – 10 min  (default 3 min)",
                 bg=BG, fg="#888888", font=("Segoe UI", 8)
                 ).grid(row=2, column=0, columnspan=3, sticky="w", padx=16, pady=(0, 8))
        self._sync_sc_state()

        # ── Random Pre-Execution Delay ─────────────────────────────────────
        tk.Frame(self, bg="#444444", height=1).grid(
            row=3, column=0, columnspan=3, sticky="ew", padx=16, pady=(4, 0))
        tk.Label(self, text="Random Pre-Execution Delay",
                 bg=BG, fg=CYAN, font=("Segoe UI", 11, "bold")
                 ).grid(row=4, column=0, columnspan=3, pady=(10, 6), padx=16, sticky="w")

        tk.Label(self, text="Enable random delay:", bg=BG, fg=FG,
                 font=FN).grid(row=5, column=0, sticky="w", **PAD)
        self._rand_en_var = tk.BooleanVar(value=self._opts.get("rand_enabled", True))
        self._rand_en_btn = tk.Button(self, font=FH, relief=tk.FLAT,
                                      cursor="hand2", width=8,
                                      command=self._toggle_rand_en)
        self._rand_en_btn.grid(row=5, column=1, sticky="w", padx=(0, 16), pady=6)
        self._refresh_rand_en_btn()

        tk.Label(self, text="Min delay (minutes):", bg=BG, fg=FG,
                 font=FN).grid(row=6, column=0, sticky="w", **PAD)
        self._min_var = tk.StringVar(value=str(self._opts.get("rand_min_min", 1)))
        self._min_sb  = tk.Spinbox(self, from_=0, to=60, width=5,
                                   textvariable=self._min_var, wrap=False,
                                   font=FNB, justify=tk.CENTER,
                                   bg=BG_PANEL, fg=GOLD, buttonbackground=BG_PANEL,
                                   relief=tk.FLAT, highlightthickness=0)
        self._min_sb.grid(row=6, column=1, sticky="w", padx=(0, 16), pady=6)

        tk.Label(self, text="Max delay (minutes):", bg=BG, fg=FG,
                 font=FN).grid(row=7, column=0, sticky="w", **PAD)
        self._max_var = tk.StringVar(value=str(self._opts.get("rand_max_min", 10)))
        self._max_sb  = tk.Spinbox(self, from_=0, to=60, width=5,
                                   textvariable=self._max_var, wrap=False,
                                   font=FNB, justify=tk.CENTER,
                                   bg=BG_PANEL, fg=GOLD, buttonbackground=BG_PANEL,
                                   relief=tk.FLAT, highlightthickness=0)
        self._max_sb.grid(row=7, column=1, sticky="w", padx=(0, 16), pady=6)

        tk.Label(self, text="Seconds: always random 00–59",
                 bg=BG, fg="#888888", font=("Segoe UI", 8)
                 ).grid(row=8, column=0, columnspan=3, sticky="w", padx=16, pady=(0, 10))

        # ── Auto-Renew Session ─────────────────────────────────────────────
        tk.Frame(self, bg="#444444", height=1).grid(
            row=9, column=0, columnspan=3, sticky="ew", padx=16, pady=(4, 0))
        tk.Label(self, text="Auto-Renew Session",
                 bg=BG, fg=CYAN, font=("Segoe UI", 11, "bold")
                 ).grid(row=10, column=0, columnspan=3, pady=(10, 4), padx=16, sticky="w")

        tk.Label(self, text="Enable auto-renew:", bg=BG, fg=FG,
                 font=FN).grid(row=11, column=0, sticky="w", **PAD)
        self._ar_en_var = tk.BooleanVar(
            value=self._opts.get("auto_renew_enabled", True))
        self._ar_en_btn = tk.Button(self, font=FH, relief=tk.FLAT,
                                    cursor="hand2", width=8,
                                    command=self._toggle_ar_en)
        self._ar_en_btn.grid(row=11, column=1, sticky="w", padx=(0, 16), pady=6)
        self._refresh_ar_en_btn()

        tk.Label(self, text="Renew when ≤ N days left:", bg=BG, fg=FG,
                 font=FN).grid(row=12, column=0, sticky="w", **PAD)
        self._ar_days_var = tk.StringVar(
            value=str(self._opts.get("auto_renew_days", 7)))
        self._ar_days_sb = tk.Spinbox(
            self, from_=1, to=30, width=5,
            textvariable=self._ar_days_var, wrap=False,
            font=FNB, justify=tk.CENTER,
            bg=BG_PANEL, fg=GOLD, buttonbackground=BG_PANEL,
            relief=tk.FLAT, highlightthickness=0)
        self._ar_days_sb.grid(row=12, column=1, sticky="w", padx=(0, 16), pady=6)
        tk.Label(self, text="Headless — no interaction needed while session is valid.",
                 bg=BG, fg="#888888", font=("Segoe UI", 8)
                 ).grid(row=13, column=0, columnspan=3, sticky="w", padx=16, pady=(0, 10))
        self._sync_ar_state()

        # ── Buttons ────────────────────────────────────────────────────────
        frm = tk.Frame(self, bg=BG)
        frm.grid(row=14, column=0, columnspan=3, pady=12)
        tk.Button(frm, text="Save", width=10, font=FH,
                  bg=GREEN, fg="white", activebackground="#1e9a50",
                  relief=tk.FLAT, cursor="hand2",
                  command=self._ok).pack(side=tk.LEFT, padx=8)
        tk.Button(frm, text="Cancel", width=10, font=FH,
                  bg="#555555", fg="white", activebackground="#666666",
                  relief=tk.FLAT, cursor="hand2",
                  command=self.destroy).pack(side=tk.LEFT, padx=8)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._sync_spinbox_state()

    def _toggle_rand_en(self):
        self._rand_en_var.set(not self._rand_en_var.get())
        self._refresh_rand_en_btn()
        self._sync_spinbox_state()

    def _toggle_sc_en(self):
        self._sc_en_var.set(not self._sc_en_var.get())
        self._refresh_sc_en_btn()
        self._sync_sc_state()

    def _refresh_sc_en_btn(self):
        on = self._sc_en_var.get()
        self._sc_en_btn.config(
            text="● ON" if on else "○ OFF",
            bg=GREEN if on else "#555555", fg="white",
            activebackground=GREEN if on else "#666666")

    def _sync_sc_state(self):
        self._sc_sb.config(
            state=tk.NORMAL if self._sc_en_var.get() else tk.DISABLED)

    def _refresh_rand_en_btn(self):
        on = self._rand_en_var.get()
        self._rand_en_btn.config(
            text="● ON" if on else "○ OFF",
            bg=GREEN if on else "#555555", fg="white",
            activebackground=GREEN if on else "#666666")

    def _sync_spinbox_state(self):
        state = tk.NORMAL if self._rand_en_var.get() else tk.DISABLED
        self._min_sb.config(state=state)
        self._max_sb.config(state=state)

    def _toggle_ar_en(self):
        self._ar_en_var.set(not self._ar_en_var.get())
        self._refresh_ar_en_btn()
        self._sync_ar_state()

    def _refresh_ar_en_btn(self):
        on = self._ar_en_var.get()
        self._ar_en_btn.config(
            text="● ON" if on else "○ OFF",
            bg=GREEN if on else "#555555", fg="white",
            activebackground=GREEN if on else "#666666")

    def _sync_ar_state(self):
        state = tk.NORMAL if self._ar_en_var.get() else tk.DISABLED
        self._ar_days_sb.config(state=state)

    def _ok(self):
        try:
            lo = max(0, int(self._min_var.get()))
            hi = max(lo, int(self._max_var.get()))
            ar_days = max(1, int(self._ar_days_var.get()))
        except ValueError:
            return
        self._on_save({"rand_enabled": self._rand_en_var.get(),
                       "rand_min_min": lo, "rand_max_min": hi,
                       "auto_renew_enabled": self._ar_en_var.get(),
                       "auto_renew_days": ar_days,
                       "startup_check_enabled": self._sc_en_var.get(),
                       "startup_check_min": max(1, min(10, int(self._sc_var.get())))})
        self.destroy()


# ── Auto-Close dialog ─────────────────────────────────────────────────────────
class _AutoCloseDialog(tk.Toplevel):
    """Tools → Auto Close: close-on-success and log-to-file checkboxes."""

    def __init__(self, parent, opts: dict, on_save):
        super().__init__(parent)
        self._on_save = on_save
        self._opts    = dict(opts)
        self.title("Auto Close")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.wm_attributes("-topmost", True)
        self.grab_set()
        if os.path.exists(_ICO_FILE):
            try:
                self.iconbitmap(_ICO_FILE)
            except Exception:
                pass
        self._build_ui()
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(
            f"+{(sw - self.winfo_width()) // 2}+{(sh - self.winfo_height()) // 2}")

    def _build_ui(self):
        FH  = ("Segoe UI", 9, "bold")
        FN  = ("Segoe UI", 10)
        FNB = ("Segoe UI", 10, "bold")
        PAD = {"padx": 16, "pady": 8}

        tk.Label(self, text="Auto Control",
                 bg=BG, fg=CYAN, font=("Segoe UI", 11, "bold")
                 ).grid(row=0, column=0, columnspan=2, pady=(14, 4), padx=16, sticky="w")

        # ── Auto close browser ───────────────────────────────────────────────
        tk.Label(self, text="Browser",
                 bg=BG, fg=CYAN, font=("Segoe UI", 11, "bold")
                 ).grid(row=1, column=0, columnspan=2, pady=(4, 4), padx=16, sticky="w")
        self._bc_en_var = tk.BooleanVar(
            value=self._opts.get("browser_close_enabled", True))
        tk.Checkbutton(
            self, text="Auto Close browser when success",
            variable=self._bc_en_var, onvalue=True, offvalue=False,
            bg=BG, fg=FG, activebackground=BG, activeforeground=CYAN,
            selectcolor=BG_PANEL, font=FN, anchor="w", cursor="hand2",
            command=self._sync_bc_state
        ).grid(row=2, column=0, columnspan=2, sticky="w", **PAD)

        tk.Label(self, text="Auto close browser after (sec):", bg=BG, fg=FG,
                 font=FN).grid(row=3, column=0, sticky="w", padx=16, pady=(0, 4))
        self._bc_secs_var = tk.StringVar(
            value=str(self._opts.get("browser_close_secs", 10)))
        self._bc_secs_sb = tk.Spinbox(
            self, from_=3, to=180, width=5, textvariable=self._bc_secs_var,
            wrap=False, font=FNB, justify=tk.CENTER,
            bg=BG_PANEL, fg=GOLD, buttonbackground=BG_PANEL,
            relief=tk.FLAT, highlightthickness=0)
        self._bc_secs_sb.grid(row=3, column=1, sticky="w", padx=(0, 16), pady=(0, 4))
        tk.Label(self, text="Range: 3 – 180 sec  (default 10 sec)",
                 bg=BG, fg="#888888", font=("Segoe UI", 8)
                 ).grid(row=4, column=0, columnspan=2, sticky="w",
                        padx=16, pady=(0, 10))
        self._sync_bc_state()

        # ── Separator ──────────────────────────────────────────────────────
        tk.Frame(self, bg="#444444", height=1).grid(
            row=5, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 6))

        # ── Close on success ───────────────────────────────────────────────
        tk.Label(self, text="Program",
                 bg=BG, fg=CYAN, font=("Segoe UI", 11, "bold")
                 ).grid(row=6, column=0, columnspan=2, pady=(4, 4), padx=16, sticky="w")
        self._cos_var = tk.BooleanVar(
            value=self._opts.get("close_on_success", False))
        tk.Checkbutton(
            self, text="Auto Close program when success",
            variable=self._cos_var, onvalue=True, offvalue=False,
            bg=BG, fg=FG, activebackground=BG, activeforeground=CYAN,
            selectcolor=BG_PANEL, font=FN, anchor="w", cursor="hand2"
        ).grid(row=7, column=0, columnspan=2, sticky="w", **PAD)
        tk.Label(self, text="Close program after (sec):", bg=BG, fg=FG,
                 font=FN).grid(row=8, column=0, sticky="w", padx=16, pady=(0, 4))
        self._cos_secs_var = tk.StringVar(
            value=str(self._opts.get("close_on_success_secs", 5)))
        tk.Spinbox(
            self, from_=3, to=180, width=5, textvariable=self._cos_secs_var,
            wrap=False, font=FNB, justify=tk.CENTER,
            bg=BG_PANEL, fg=GOLD, buttonbackground=BG_PANEL,
            relief=tk.FLAT, highlightthickness=0
        ).grid(row=8, column=1, sticky="w", padx=(0, 16), pady=(0, 4))
        tk.Label(self, text="Range: 3 – 180 sec  (default 5 sec)",
                 bg=BG, fg="#888888", font=("Segoe UI", 8)
                 ).grid(row=9, column=0, columnspan=2, sticky="w",
                        padx=16, pady=(0, 10))

        # ── Auto close on timeout ──────────────────────────────────────────
        tk.Frame(self, bg="#333333", height=1).grid(
            row=10, column=0, columnspan=2, sticky="ew", padx=24, pady=(0, 4))
        self._act_var = tk.BooleanVar(
            value=self._opts.get("auto_close_timeout_enabled", False))
        tk.Checkbutton(
            self, text="Auto Close program on timeout",
            variable=self._act_var, onvalue=True, offvalue=False,
            bg=BG, fg=FG, activebackground=BG, activeforeground=CYAN,
            selectcolor=BG_PANEL, font=FN, anchor="w", cursor="hand2",
            command=self._sync_act_state
        ).grid(row=11, column=0, columnspan=2, sticky="w", **PAD)
        tk.Label(self, text="Close program after (min):", bg=BG, fg=FG,
                 font=FN).grid(row=12, column=0, sticky="w", padx=16, pady=(0, 4))
        self._act_min_var = tk.StringVar(
            value=str(self._opts.get("auto_close_timeout_min", 15)))
        self._act_min_sb = tk.Spinbox(
            self, from_=5, to=60, width=5, textvariable=self._act_min_var,
            wrap=False, font=FNB, justify=tk.CENTER,
            bg=BG_PANEL, fg=GOLD, buttonbackground=BG_PANEL,
            relief=tk.FLAT, highlightthickness=0)
        self._act_min_sb.grid(row=12, column=1, sticky="w", padx=(0, 16), pady=(0, 4))
        tk.Label(self, text="Range: 5 – 60 min  (default 15 min)",
                 bg=BG, fg="#888888", font=("Segoe UI", 8)
                 ).grid(row=13, column=0, columnspan=2, sticky="w",
                        padx=16, pady=(0, 10))
        self._sync_act_state()

        # ── Separator ──────────────────────────────────────────────────────
        tk.Frame(self, bg="#444444", height=1).grid(
            row=14, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 6))

        # ── Log to file ────────────────────────────────────────────────────
        tk.Label(self, text="Logging",
                 bg=BG, fg=CYAN, font=("Segoe UI", 11, "bold")
                 ).grid(row=15, column=0, columnspan=2, pady=(4, 4), padx=16, sticky="w")
        self._log_var = tk.BooleanVar(
            value=self._opts.get("log_enabled", True))
        tk.Checkbutton(
            self, text="Save log to log.log  (default: on)",
            variable=self._log_var, onvalue=True, offvalue=False,
            bg=BG, fg=FG, activebackground=BG, activeforeground=CYAN,
            selectcolor=BG_PANEL, font=FN, anchor="w", cursor="hand2"
        ).grid(row=16, column=0, columnspan=2, sticky="w", **PAD)
        tk.Label(self, text="Logs: startup/close · session state · target time · fire time · result.",
                 bg=BG, fg="#888888", font=("Segoe UI", 8)
                 ).grid(row=17, column=0, columnspan=2, sticky="w",
                        padx=16, pady=(0, 12))

        # ── Buttons ────────────────────────────────────────────────────────
        frm = tk.Frame(self, bg=BG)
        frm.grid(row=18, column=0, columnspan=2, pady=12)
        tk.Button(frm, text="Save", width=10, font=FH,
                  bg=GREEN, fg="white", activebackground="#1e9a50",
                  relief=tk.FLAT, cursor="hand2",
                  command=self._ok).pack(side=tk.LEFT, padx=8)
        tk.Button(frm, text="Cancel", width=10, font=FH,
                  bg="#555555", fg="white", activebackground="#666666",
                  relief=tk.FLAT, cursor="hand2",
                  command=self.destroy).pack(side=tk.LEFT, padx=8)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _ok(self):
        try:
            bc_secs = max(3, min(180, int(self._bc_secs_var.get())))
        except ValueError:
            bc_secs = 10
        try:
            cos_secs = max(3, min(180, int(self._cos_secs_var.get())))
        except ValueError:
            cos_secs = 5
        try:
            act_min = max(5, min(60, int(self._act_min_var.get())))
        except ValueError:
            act_min = 15
        self._opts["browser_close_enabled"]      = self._bc_en_var.get()
        self._opts["browser_close_secs"]         = bc_secs
        self._opts["close_on_success"]           = self._cos_var.get()
        self._opts["close_on_success_secs"]      = cos_secs
        self._opts["auto_close_timeout_enabled"] = self._act_var.get()
        self._opts["auto_close_timeout_min"]     = act_min
        self._opts["log_enabled"]                = self._log_var.get()
        self._on_save(self._opts)
        self.destroy()

    def _sync_bc_state(self):
        self._bc_secs_sb.config(
            state=tk.NORMAL if self._bc_en_var.get() else tk.DISABLED)

    def _sync_act_state(self):
        self._act_min_sb.config(
            state=tk.NORMAL if self._act_var.get() else tk.DISABLED)


# ── Email Alert helpers ────────────────────────────────────────────────────────
def _save_notify_to(addresses: list[str]):
    """Rewrite the NOTIFY_TO line in user.env with the given list (max 5)."""
    env_file = os.path.join(HERE, "user.env")
    new_val  = ", ".join(a.strip() for a in addresses if a.strip())
    lines = []
    found = False
    if os.path.exists(env_file):
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                stripped = line.rstrip("\n")
                if stripped.lstrip().startswith("NOTIFY_TO"):
                    lines.append(f"NOTIFY_TO={new_val}\n")
                    found = True
                else:
                    lines.append(line if line.endswith("\n") else line + "\n")
    if not found:
        lines.append(f"NOTIFY_TO={new_val}\n")
    with open(env_file, "w", encoding="utf-8") as f:
        f.writelines(lines)
    # update live config so next alert uses the new list
    SMTP_CFG["to"] = [a.strip() for a in new_val.split(",") if a.strip()][:5]


def _save_smtp_cfg(host: str, port: int, user: str, password: str):
    """Rewrite SMTP_HOST/PORT/USER/PASS lines in user.env and update SMTP_CFG live."""
    env_file = os.path.join(HERE, "user.env")
    keys = {
        "SMTP_HOST": host.strip(),
        "SMTP_PORT": str(port),
        "SMTP_USER": user.strip(),
        "SMTP_PASS": password,
    }
    lines = []
    found = {k: False for k in keys}
    if os.path.exists(env_file):
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                raw = line.rstrip("\n")
                matched = False
                for k in keys:
                    if raw.lstrip().startswith(k + "=") or raw.lstrip() == k:
                        lines.append(f"{k}={keys[k]}\n")
                        found[k] = True
                        matched = True
                        break
                if not matched:
                    lines.append(line if line.endswith("\n") else line + "\n")
    for k, v in keys.items():
        if not found[k]:
            lines.append(f"{k}={v}\n")
    with open(env_file, "w", encoding="utf-8") as f:
        f.writelines(lines)
    # update live config
    SMTP_CFG["host"]     = host.strip()
    SMTP_CFG["port"]     = port
    SMTP_CFG["user"]     = user.strip()
    SMTP_CFG["password"] = password


# ── Email Alert dialog ─────────────────────────────────────────────────────────
class _EmailAlertDialog(tk.Toplevel):
    """Tools → Email Alert: manage recipients and test the SMTP config."""

    MAX = 5

    def __init__(self, parent, log_fn):
        super().__init__(parent)
        self._log = log_fn
        self.title("Email Alert")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.wm_attributes("-topmost", True)
        self.grab_set()
        if os.path.exists(_ICO_FILE):
            try:
                self.iconbitmap(_ICO_FILE)
            except Exception:
                pass
        self._addrs: list[str] = list(SMTP_CFG.get("to", []))
        self._build_ui()
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(
            f"+{(sw - self.winfo_width()) // 2}+{(sh - self.winfo_height()) // 2}")

    def _build_ui(self):
        FH  = ("Segoe UI", 9, "bold")
        FN  = ("Segoe UI", 9)
        FNS = ("Segoe UI", 8)

        def _entry(parent, var, width=28, show=""):
            e = tk.Entry(parent, textvariable=var, width=width, show=show,
                         font=FN, bg=BG_PANEL, fg=FG, insertbackground=FG,
                         relief=tk.FLAT, highlightthickness=1,
                         highlightbackground="#555555", highlightcolor=CYAN)
            e.pack(side=tk.LEFT, ipady=3)
            return e

        def _sep():
            tk.Frame(self, bg="#444444", height=1).pack(fill=tk.X, padx=14, pady=(4, 6))

        # ── SMTP Server Settings ───────────────────────────────────────────
        tk.Label(self, text="SMTP Server Settings",
                 bg=BG, fg=CYAN, font=("Segoe UI", 11, "bold")
                 ).pack(anchor="w", padx=14, pady=(12, 4))

        # Host + Port on one row
        hp_row = tk.Frame(self, bg=BG)
        hp_row.pack(fill=tk.X, padx=14, pady=(0, 4))
        tk.Label(hp_row, text="Host:", bg=BG, fg=FG, font=FN, width=9,
                 anchor="w").pack(side=tk.LEFT)
        self._host_var = tk.StringVar(value=SMTP_CFG.get("host", ""))
        _entry(hp_row, self._host_var, width=22)
        tk.Label(hp_row, text="  Port:", bg=BG, fg=FG, font=FN).pack(side=tk.LEFT)
        self._port_var = tk.StringVar(value=str(SMTP_CFG.get("port", 587)))
        tk.Entry(hp_row, textvariable=self._port_var, width=6,
                 font=FN, bg=BG_PANEL, fg=FG, insertbackground=FG,
                 relief=tk.FLAT, highlightthickness=1,
                 highlightbackground="#555555", highlightcolor=CYAN,
                 justify=tk.CENTER
                 ).pack(side=tk.LEFT, padx=(4, 0), ipady=3)

        # From (SMTP_USER)
        from_row = tk.Frame(self, bg=BG)
        from_row.pack(fill=tk.X, padx=14, pady=(0, 4))
        tk.Label(from_row, text="From (user):", bg=BG, fg=FG, font=FN, width=12,
                 anchor="w").pack(side=tk.LEFT)
        self._user_var = tk.StringVar(value=SMTP_CFG.get("user", ""))
        _entry(from_row, self._user_var, width=30)

        # Password + show/hide toggle
        pw_row = tk.Frame(self, bg=BG)
        pw_row.pack(fill=tk.X, padx=14, pady=(0, 2))
        tk.Label(pw_row, text="App Password:", bg=BG, fg=FG, font=FN, width=12,
                 anchor="w").pack(side=tk.LEFT)
        self._pass_var  = tk.StringVar(value=SMTP_CFG.get("password", ""))
        self._pass_show = False
        self._pass_ent  = _entry(pw_row, self._pass_var, width=24, show="•")
        self._show_btn  = tk.Button(
            pw_row, text="Show", font=FNS, width=5,
            bg="#3a3a3a", fg=FG, activebackground="#4a4a4a",
            relief=tk.FLAT, cursor="hand2",
            command=self._toggle_pass_show)
        self._show_btn.pack(side=tk.LEFT, padx=(6, 0))

        # Gmail help tip
        tk.Label(self,
                 text=("ℹ  For Gmail: generate an App Password at\n"
                       "   myaccount.google.com → Security →"
                       " 2-Step Verification → App passwords"),
                 bg=BG, fg="#888888", font=FNS, justify=tk.LEFT
                 ).pack(anchor="w", padx=14, pady=(2, 6))

        _sep()

        # ── Recipients ────────────────────────────────────────────────────
        tk.Label(self, text="Alert Recipients",
                 bg=BG, fg=CYAN, font=("Segoe UI", 11, "bold")
                 ).pack(anchor="w", padx=14, pady=(0, 2))

        tk.Label(self,
                 text=("Alert is sent when the pre-check (1 h before slot) finds the session expired.\n"
                       "Act promptly — use 🔄 Renew Auth to restore a valid session."),
                 bg=BG, fg="#aaaaaa", font=FNS, justify=tk.LEFT
                 ).pack(anchor="w", padx=14, pady=(0, 6))

        list_frame = tk.Frame(self, bg=BG)
        list_frame.pack(fill=tk.X, padx=14)
        self._rows: list[dict] = []
        self._list_frame = list_frame
        for addr in self._addrs:
            self._add_row(addr)

        self._add_btn = tk.Button(
            self, text="+ Add address", font=FN,
            bg="#3a3a3a", fg=FG, activebackground="#4a4a4a",
            relief=tk.FLAT, cursor="hand2", command=self._on_add)
        self._add_btn.pack(anchor="w", padx=14, pady=(4, 8))
        self._refresh_add_btn()

        _sep()

        # ── button row ────────────────────────────────────────────────────
        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(pady=(0, 12))

        tk.Button(btn_row, text="Save", width=9, font=FH,
                  bg=GREEN, fg="white", activebackground="#1e9a50",
                  relief=tk.FLAT, cursor="hand2",
                  command=self._save).pack(side=tk.LEFT, padx=6)

        self._test_btn = tk.Button(btn_row, text="Send Test", width=10, font=FH,
                  bg="#1e6fb5", fg="white", activebackground="#155a94",
                  relief=tk.FLAT, cursor="hand2",
                  command=self._test)
        self._test_btn.pack(side=tk.LEFT, padx=6)

        tk.Button(btn_row, text="Cancel", width=9, font=FH,
                  bg="#555555", fg="white", activebackground="#666666",
                  relief=tk.FLAT, cursor="hand2",
                  command=self.destroy).pack(side=tk.LEFT, padx=6)

        self.protocol("WM_DELETE_WINDOW", self.destroy)

    # ── SMTP field helpers ─────────────────────────────────────────────────
    def _toggle_pass_show(self):
        self._pass_show = not self._pass_show
        self._pass_ent.config(show="" if self._pass_show else "•")
        self._show_btn.config(text="Hide" if self._pass_show else "Show")

    def _smtp_from_fields(self) -> tuple[str, int, str, str]:
        host = self._host_var.get().strip()
        try:
            port = int(self._port_var.get().strip())
        except ValueError:
            port = 587
        user = self._user_var.get().strip()
        pw   = self._pass_var.get()
        return host, port, user, pw

    def _add_row(self, text: str = ""):
        row = tk.Frame(self._list_frame, bg=BG)
        row.pack(fill=tk.X, pady=2)
        var = tk.StringVar(value=text)
        ent = tk.Entry(row, textvariable=var, width=34,
                       font=("Segoe UI", 9),
                       bg=BG_PANEL, fg=FG, insertbackground=FG,
                       relief=tk.FLAT, highlightthickness=1,
                       highlightbackground="#555555",
                       highlightcolor=CYAN)
        ent.pack(side=tk.LEFT, padx=(0, 6), ipady=3)
        del_btn = tk.Button(row, text="✕", width=2,
                            font=("Segoe UI", 8, "bold"),
                            bg="#6b3030", fg="white",
                            activebackground="#8b2020",
                            relief=tk.FLAT, cursor="hand2")
        del_btn.pack(side=tk.LEFT)
        r = {"frame": row, "var": var, "entry": ent, "del_btn": del_btn}
        del_btn.config(command=lambda rd=r: self._del_row(rd))
        self._rows.append(r)

    def _del_row(self, rd: dict):
        rd["frame"].destroy()
        self._rows.remove(rd)
        self._refresh_add_btn()

    def _on_add(self):
        if len(self._rows) >= self.MAX:
            return
        self._add_row()
        self._refresh_add_btn()

    def _refresh_add_btn(self):
        self._add_btn.config(
            state=tk.NORMAL if len(self._rows) < self.MAX else tk.DISABLED)

    def _current_addrs(self) -> list[str]:
        return [r["var"].get().strip() for r in self._rows
                if r["var"].get().strip()]

    def _save(self):
        host, port, user, pw = self._smtp_from_fields()
        _save_smtp_cfg(host, port, user, pw)
        addrs = self._current_addrs()[:self.MAX]
        _save_notify_to(addrs)
        self._log(
            f"[Email] SMTP saved: host={host or '(empty)'} port={port} "
            f"user={user or '(empty)'} pass={'***' if pw else '(empty)'}.")
        self._log(f"[Email] Recipients saved: {', '.join(addrs) if addrs else '(none)'}")
        self.destroy()

    def _test(self):
        host, port, user, pw = self._smtp_from_fields()
        addrs = self._current_addrs()
        if not addrs:
            self._log("[Email] No recipients — add at least one address.")
            return
        if not all([host, user, pw]):
            self._log("[Email] SMTP host/user/password missing — fill in and Save first.")
            return
        test_cfg = {"host": host, "port": port, "user": user,
                    "password": pw, "to": addrs}
        self._test_btn.config(state=tk.DISABLED, text="Sending…")
        self._log(f"[Email] Sending test email to: {', '.join(addrs)} …")
        def worker():
            try:
                msg = MIMEText(
                    f"AutoID test email\nSent at : {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    "If you received this, your SMTP alert config is working correctly.",
                    "plain", "utf-8")
                msg["Subject"] = "[AutoID] Test alert email"
                msg["From"]    = test_cfg["user"]
                msg["To"]      = ", ".join(test_cfg["to"])
                with smtplib.SMTP(test_cfg["host"], test_cfg["port"], timeout=15) as s:
                    s.ehlo(); s.starttls()
                    s.login(test_cfg["user"], test_cfg["password"])
                    s.sendmail(test_cfg["user"], test_cfg["to"], msg.as_string())
                self.after(0, lambda: (
                    self._log("[Email] Test email sent successfully."),
                    self._test_btn.config(state=tk.NORMAL, text="Send Test")))
            except Exception as exc:
                self.after(0, lambda e=exc: (
                    self._log(f"[Email] Test FAILED: {e}"),
                    self._test_btn.config(state=tk.NORMAL, text="Send Test")))
        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    App().mainloop()
