#!/usr/bin/env python3
"""
nursery_deck.py — a Stream Deck nursery panel for Sprout Track.

Turns a 15-key Elgato Stream Deck on a Raspberry Pi into a tap-to-log panel
for the Sprout Track Webhook API (/api/hooks/v1): big tactile actions plus
live at-a-glance status, sitting on the nursery dresser.

Drives the deck raw over USB HID via python-elgato-streamdeck. The Elgato
plugin SDK only runs inside Elgato's desktop app (Windows/macOS), so pages
here are this daemon's own state machine — pressing a category repaints all
15 keys from a different dict.

Configuration is entirely by environment variable (see CONFIG below); the
defaults are placeholders. Setup, deployment, troubleshooting, and notes on
API behaviour are in docs/IMPLEMENTATION.md.

Run:  ST_API_KEY=... ST_BABY_ID=... python3 nursery_deck.py
"""

from __future__ import annotations

import os
import sys
import time
import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

import requests

try:
    from StreamDeck.DeviceManager import DeviceManager
    from StreamDeck.ImageHelpers import PILHelper
except ImportError as e:
    sys.exit(f"Cannot import StreamDeck ({e})\n"
             "  pip install streamdeck; apt install libhidapi-libusb0")

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError as e:
    sys.exit(f"Cannot import Pillow ({e})\n"
             "  pip install pillow; apt install libopenjp2-7 libtiff6 libwebp7")


# =====================================================================
# CONFIG  — edit these (or override via environment variables)
# =====================================================================
BASE_URL   = os.getenv("ST_BASE_URL", "https://sprout-track.com")
API_KEY    = os.getenv("ST_API_KEY",  "st_live_REPLACE_ME")
# Optional: if unset, the first baby from GET /babies is used.
BABY_ID    = os.getenv("ST_BABY_ID",  "REPLACE_WITH_BABY_ID")
TIMEZONE   = os.getenv("ST_TIMEZONE", "America/Chicago")
ICON_DIR   = os.getenv("ST_ICON_DIR",
                       os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets"))

POLL_SECONDS      = 30          # GET /status cadence (limit is 60/min per key)
AUTO_HOME_SECONDS = 2.0         # return to home after a successful log
FEED_STEP_OZ      = 0.5         # ± step for feed amount
FEED_DEFAULT_OZ   = 4.0
# Diaper condition/color are free-form strings with no validation and no
# /reference endpoint -- a typo is accepted and stored silently. These mirror
# the app's own dropdowns, upper-cased to match what the app writes.
# The app's "Other" option is omitted: a deck key labelled "Other" carries no
# information later, and the app is the better place for an unusual entry.
# The app's "Diaper Cream Applied" checkbox has no webhook API field at all.
DIAPER_CONDITIONS = [("Normal", "NORMAL"), ("Loose", "LOOSE"), ("Firm", "FIRM")]
DIAPER_COLORS = [("Yellow", "YELLOW"), ("Brown", "BROWN"), ("Green", "GREEN"),
                 ("Black", "BLACK"), ("Red", "RED")]

DIM_START_HOUR    = 21          # 9pm  -> dim
DIM_END_HOUR      = 7           # 7am  -> bright
BRIGHT_DAY        = 70
BRIGHT_NIGHT      = 15
OUTBOX_MAX_ATTEMPTS = 20        # give up on a queued POST after this many tries
OUTBOX_MAX_BACKOFF  = 60.0      # seconds; retry sweep backs off 2s -> 60s

API_ROOT = f"{BASE_URL}/api/hooks/v1"
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}


# =====================================================================
# ICONS — logical name -> filename in ICON_DIR
# =====================================================================
# Only the glyphs the deck actually uses. Sprout Track's public/ folder has
# more (med, measurement, note, milestone, vaccine, activity, photo) if a
# removed flow is ever reinstated.
ICONS = {
    "feed":       "bottle-128.png",
    "breast_l":   "breastfeed-128-L.png",
    "breast_r":   "breastfeed-128-R.png",
    "solids":     "food-128.png",
    "diaper":     "diaper-128.png",
    "sleep":      "crib-128.png",
    "pump":       "pump-128.png",
    "bath":       "bath-128.png",
    "settings":   "config-128.png",
    "brand":      "sprout-128.png",
}

# Sleep locations: deck label -> the exact value the API expects.
# Labels are shortened to fit a 72px key, but the POST must carry the canonical
# name from GET /reference?type=sleep-locations or the server won't match it.
# The reference list also contains an "Other" catch-all and a "CAR_SEAT"
# duplicate of "Car Seat"; both are deliberately left off the deck.
PICKER_TEXT = 17        # shared font ceiling for selector keys

# bathType is free-form and has no /reference endpoint, so these mirror the
# app's built-in choices. Deck label -> value sent.
BATH_TYPES = [
    ("Full",   "Full Bath"),
    ("Sponge", "Sponge Bath"),
    ("Wipe",   "Wipe Down"),
]
PUMP_STEP_OZ = 0.5      # ± step when entering pumped amounts

SLEEP_LOCATIONS = [
    ("Crib",     "Crib"),
    ("Bassinet", "Bassinet"),
    ("Parents",  "Parents Room"),
    ("Car Seat", "Car Seat"),
    ("Stroller", "Stroller"),
    ("Contact",  "Contact"),
]

# Colors
BG        = (18, 22, 20)
BG_ACTIVE = (30, 70, 45)     # green-ish when a timer is running
BG_SELECT = (28, 60, 95)     # blue: chosen on a picker, but NOT yet committed
BG_GO     = (30, 100, 50)    # the key that actually writes to the API
BG_WARN   = (110, 45, 25)    # amber-red: overdue per the family's own thresholds
BG_OK     = (25, 90, 40)     # flash after a successful log
BG_ERR    = (90, 30, 30)
FG        = (235, 235, 230)
FG_DIM    = (150, 155, 150)


def _load_font(size: int) -> "ImageFont.FreeTypeFont":
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


_font_cache: dict[int, "ImageFont.FreeTypeFont"] = {}


def font(size: int) -> "ImageFont.FreeTypeFont":
    if size not in _font_cache:
        _font_cache[size] = _load_font(size)
    return _font_cache[size]


def _fit(draw, text: str, max_w: int, start: int, floor: int = 8):
    """Largest font from `start` down to `floor` whose text fits max_w."""
    size = start
    while size > floor and draw.textlength(text, font=font(size)) > max_w:
        size -= 1
    return font(size)

_icon_cache: dict[str, "Image.Image"] = {}


def _load_icon(name: Optional[str]) -> Optional["Image.Image"]:
    if not name or name not in ICONS:
        return None
    if name in _icon_cache:
        return _icon_cache[name]
    path = os.path.join(ICON_DIR, ICONS[name])
    try:
        img = Image.open(path).convert("RGBA")
        _icon_cache[name] = img
        return img
    except FileNotFoundError:
        print(f"[warn] icon not found: {path}")
        return None


# =====================================================================
# RENDERING — one key face = background + icon + label + optional live value
# =====================================================================
def render_key(deck, icon: Optional[str], label: str,
               value: Optional[str] = None, bg=BG,
               text_max: Optional[int] = None) -> bytes:
    image = PILHelper.create_image(deck, background=bg)
    draw = ImageDraw.Draw(image)
    w, h = image.size
    margin = 2                       # keep glyphs off the bezel
    ic = _load_icon(icon)

    if ic is None:
        # Text-only key (± steppers, amount readouts): centre it large instead
        # of tucking a tiny caption at the bottom edge.
        big = text_max or 24
        if value and label:
            f_v = _fit(draw, value, w - 2 * margin, min(22, big))
            f_l = _fit(draw, label, w - 2 * margin, 14)
            draw.text((w / 2, h / 2 - 8), value, font=f_v, fill=FG, anchor="mm")
            draw.text((w / 2, h / 2 + 14), label, font=f_l, fill=FG_DIM, anchor="mm")
        elif value or label:
            text = value or label
            draw.text((w / 2, h / 2), text,
                      font=_fit(draw, text, w - 2 * margin, big),
                      fill=FG, anchor="mm")
        return PILHelper.to_native_format(deck, image)

    # Icon key: value badge on top, icon in the middle, label on the bottom.
    label_h = 15 if label else 0
    value_h = 17 if value else 0
    box = min(w - 2 * margin, h - label_h - value_h - 2 * margin)
    if box > 0:
        ic_resized = ic.resize((box, box), Image.LANCZOS)
        image.paste(ic_resized, ((w - box) // 2, value_h + margin), ic_resized)

    if value:
        draw.text((w / 2, value_h / 2 + 1), value,
                  font=_fit(draw, value, w - 2 * margin, 15),
                  fill=FG, anchor="mm")

    if label:
        # anchor="ms" puts the text baseline at y, leaving room for descenders
        draw.text((w / 2, h - 4), label,
                  font=_fit(draw, label, w - 2 * margin, 13),
                  fill=FG, anchor="ms")

    return PILHelper.to_native_format(deck, image)


def fmt_mins(minutes: Optional[float]) -> str:
    if minutes is None:
        return "--"
    minutes = int(minutes)
    if minutes < 60:
        return f"{minutes}m"
    if minutes < 24 * 60:
        return f"{minutes // 60}h{minutes % 60:02d}"
    # Past a day the hour count stops being readable at a glance ("103h03").
    days, rem_h = divmod(minutes // 60, 24)
    return f"{days}d{rem_h}h"


def fmt_secs(seconds: Optional[float]) -> str:
    if seconds is None:
        return "0:00"
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


# =====================================================================
# HOOKS API CLIENT — GET status/reference, POST activities, offline outbox
# =====================================================================
class HooksClient:
    def __init__(self, baby_id: str = ""):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        # Mutable so the deck can switch babies without rebuilding the client.
        self.baby_id = baby_id or BABY_ID
        self.outbox: "queue.Queue[dict]" = queue.Queue()
        self._start_outbox_worker()

    @property
    def base(self) -> str:
        return f"{API_ROOT}/babies/{self.baby_id}"

    # ---- reads -------------------------------------------------------
    def get_babies(self) -> list:
        """Babies this key can see. A baby-scoped key returns just the one."""
        try:
            r = self.session.get(f"{API_ROOT}/babies", timeout=5)
            r.raise_for_status()
            return r.json().get("data", {}).get("babies", [])
        except requests.RequestException as e:
            print(f"[warn] baby list fetch failed: {e}")
            return []

    def get_status(self) -> Optional[dict]:
        try:
            r = self.session.get(f"{self.base}/status",
                                 params={"timezone": TIMEZONE}, timeout=5)
            r.raise_for_status()
            return r.json().get("data")
        except requests.RequestException as e:
            print(f"[warn] status poll failed: {e}")
            return None

    # ---- writes (with offline queue) --------------------------------
    def post_activity(self, payload: dict,
                      on_result: Optional[Callable[[bool], None]] = None):
        """Fire-and-forget POST. Queues + retries on network failure so a
        press during a wifi blip still lands (local-first ethos)."""
        self.outbox.put({"payload": payload, "cb": on_result})

    def _start_outbox_worker(self):
        def worker():
            pending: list[dict] = []
            backoff = 0.0          # seconds to wait before the next retry sweep
            next_try = 0.0
            while True:
                try:
                    item = self.outbox.get(timeout=2)
                    item["attempts"] = 0
                    pending.append(item)
                    next_try = 0.0          # new work: retry sweep immediately
                    backoff = 0.0
                except queue.Empty:
                    pass
                if not pending or time.monotonic() < next_try:
                    continue
                still: list[dict] = []
                for item in pending:
                    if self._try_post(item):
                        if item["cb"]:
                            item["cb"](True)
                    else:
                        item["attempts"] += 1
                        if item["attempts"] < OUTBOX_MAX_ATTEMPTS:
                            still.append(item)   # keep for retry
                        else:
                            print("[err] dropping activity after "
                                  f"{OUTBOX_MAX_ATTEMPTS} attempts: {item['payload']}")
                            if item["cb"]:
                                item["cb"](False)
                pending = still
                if pending:
                    backoff = min(max(backoff * 2, 2.0), OUTBOX_MAX_BACKOFF)
                    next_try = time.monotonic() + backoff
                else:
                    backoff = 0.0
        threading.Thread(target=worker, daemon=True).start()

    def _try_post(self, item: dict) -> bool:
        try:
            r = self.session.post(f"{self.base}/activities",
                                  json=item["payload"], timeout=5)
            if r.status_code == 429:
                print("[warn] rate limited; will retry")
                return False
            if 400 <= r.status_code < 500:
                # Permanent: bad payload, FEED_ALREADY_ACTIVE, unknown medicine.
                # Retrying only burns the rate limit, so drop it.
                print(f"[err] rejected ({r.status_code}): {r.text[:200]}")
                item["attempts"] = OUTBOX_MAX_ATTEMPTS
                return False
            r.raise_for_status()
            return True
        except requests.RequestException as e:
            print(f"[warn] post failed (queued for retry): {e}")
            return False


# =====================================================================
# KEY / PAGE MODEL
# =====================================================================
@dataclass
class Key:
    icon: Optional[str] = None
    label: str = ""
    # dynamic bits receive the app so they can read live state:
    label_fn: Optional[Callable[["App"], str]] = None
    value_fn: Optional[Callable[["App"], Optional[str]]] = None
    bg_fn: Optional[Callable[["App"], tuple]] = None
    on_press: Optional[Callable[["App"], None]] = None
    # Cap for text-only keys. Auto-fit maximises per key, which makes a row of
    # mixed-length picker labels ("Nap" vs "Bassinet") look ragged; capping a
    # group to the same ceiling keeps the row visually even.
    text_max: Optional[int] = None


BLANK = Key()


# =====================================================================
# APP — device setup, state machine, poller, dim schedule
# =====================================================================
class App:
    def __init__(self):
        self.client = HooksClient()
        self.status: dict = {}          # last GET /status snapshot
        self.status_at: float = 0.0     # monotonic time of snapshot
        self.page: str = "home"
        self.feed_oz: float = FEED_DEFAULT_OZ
        # Sleep picker selection. Sticky: the last combination used becomes the
        # next default, so a repeat of the same kind of sleep is one extra press.
        self.sleep_type: str = "NAP"
        self.sleep_location: str = "Crib"
        self.pump_left: float = 0.0     # oz entered on the pump end screen
        self.pump_right: float = 0.0
        # Bath picker. soap/shampoo default to True to match the API's own
        # defaults, so an untouched screen logs what the server would anyway.
        # Diaper modifiers. All reset after a log so the default stays "plain
        # diaper, one press"; condition/color are omitted entirely when unset.
        self.diaper_blowout: bool = False
        self.diaper_condition: Optional[str] = None
        self.diaper_color: Optional[str] = None
        self.babies: list = []          # from GET /babies at startup
        self.bath_type: str = "Full Bath"
        self.bath_soap: bool = True
        self.bath_shampoo: bool = True
        self._flash: dict[int, tuple[float, tuple]] = {}  # key -> (until, bg)
        self.deck = None
        self._paint_lock = threading.Lock()
        self._refresh = threading.Event()   # set after a write, to re-poll early
        self._painted: dict[int, tuple] = {}   # idx -> last painted key state
        self.pages = build_pages()

    # ---- device ------------------------------------------------------
    def open_deck(self):
        decks = DeviceManager().enumerate()
        if not decks:
            sys.exit("No Stream Deck found. Check the powered hub and udev rule.")
        self.deck = decks[0]
        self.deck.open()
        self.deck.reset()
        print(f"Connected: {self.deck.deck_type()} "
              f"({self.deck.key_count()} keys)")
        self.deck.set_key_callback(self._on_key)
        self._apply_brightness()

    def _apply_brightness(self):
        hour = datetime.now().hour
        night = hour >= DIM_START_HOUR or hour < DIM_END_HOUR
        self.deck.set_brightness(BRIGHT_NIGHT if night else BRIGHT_DAY)

    # ---- status helpers (read from last snapshot) --------------------
    def last(self, kind: str) -> dict:
        return (self.status.get("lastActivities") or {}).get(kind) or {}

    def active_feed(self) -> Optional[dict]:
        return self.status.get("activeFeed")

    def sleep_active(self) -> bool:
        return bool(self.last("sleep").get("isActive"))

    def pump_active(self) -> bool:
        return bool(self.last("pump").get("isActive"))

    def baby_name(self) -> str:
        for b in self.babies:
            if b.get("id") == self.client.baby_id:
                return b.get("firstName") or "Baby"
        return "Sprout"

    def switch_baby(self, baby_id: str):
        """Point the client at another baby and invalidate everything cached."""
        if baby_id == self.client.baby_id:
            self.goto("home")
            return
        self.client.baby_id = baby_id
        self.status = {}          # counts/timers belong to the previous baby
        self.status_at = 0.0
        self._painted.clear()     # force a full repaint, not a diff
        self._refresh.set()       # re-poll now instead of waiting out the cycle
        self.goto("home")

    def daily(self, k: str):
        return (self.status.get("dailyCounts") or {}).get(k)

    def _elapsed(self, since_snapshot: bool = True) -> float:
        return time.monotonic() - self.status_at if since_snapshot else 0.0

    def feed_elapsed_secs(self) -> Optional[float]:
        af = self.active_feed()
        if not af:
            return None
        base = (af.get("leftDuration", 0) or 0) + (af.get("rightDuration", 0) or 0)
        if af.get("isPaused"):
            return base
        return base + self._elapsed()   # tick locally between polls

    # ---- painting ----------------------------------------------------
    def paint(self):
        with self._paint_lock:
            self._paint_locked()

    def _paint_locked(self):
        """Repaint only keys whose face actually changed.

        run_loop() calls this every second so running timers tick, but most
        seconds nothing moves. Rendering (Pillow composite + JPEG) and the USB
        transfer are the expensive part, so diff on the inputs and skip both.
        """
        page = self.pages[self.page]
        for idx in range(self.deck.key_count()):
            key = page.get(idx, BLANK)
            label = key.label_fn(self) if key.label_fn else key.label
            value = key.value_fn(self) if key.value_fn else None
            bg = key.bg_fn(self) if key.bg_fn else BG
            if idx in self._flash and self._flash[idx][0] > time.monotonic():
                bg = self._flash[idx][1]
            state = (self.page, key.icon, label, value, bg, key.text_max)
            if self._painted.get(idx) == state:
                continue
            self.deck.set_key_image(
                idx, render_key(self.deck, key.icon, label, value, bg, key.text_max))
            self._painted[idx] = state

    def flash(self, idx: int, ok: bool = True):
        self._flash[idx] = (time.monotonic() + 0.8, BG_OK if ok else BG_ERR)
        self._painted.pop(idx, None)

    def goto(self, page: str):
        self.page = page
        self.paint()

    def go_home_soon(self):
        def _t():
            time.sleep(AUTO_HOME_SECONDS)
            if self.page != "home":
                self.goto("home")
        threading.Thread(target=_t, daemon=True).start()

    # ---- logging convenience ----------------------------------------
    def log(self, idx: int, payload: dict, return_home: bool = True):
        self.flash(idx, ok=True)
        self.paint()
        self.client.post_activity(payload, on_result=lambda ok: self._refresh.set())
        if return_home:
            self.go_home_soon()

    # ---- input -------------------------------------------------------
    def _on_key(self, deck, idx, pressed):
        if not pressed:
            return
        key = self.pages[self.page].get(idx, BLANK)
        if key.on_press:
            try:
                key.on_press(self)
            except Exception as e:   # never let a bad handler kill the loop
                print(f"[err] handler: {e}")
                self.flash(idx, ok=False)
                self.paint()

    # ---- background loop: poll + local timer tick + dim --------------
    def run_loop(self):
        # Resolve the baby list once. A baby-scoped key returns a single entry,
        # in which case the switcher stays unreachable.
        self.babies = self.client.get_babies()
        if self.babies and not any(b.get("id") == self.client.baby_id
                                   for b in self.babies):
            # ST_BABY_ID unset or not visible to this key: fall back to the first.
            self.client.baby_id = self.babies[0].get("id")
        self.pages = build_pages(self.babies)
        print(f"Babies: {[b.get('firstName') for b in self.babies]} "
              f"-> using {self.baby_name()}")

        last_poll = 0.0
        while True:
            now = time.monotonic()
            if now - last_poll >= POLL_SECONDS or self._refresh.is_set():
                self._refresh.clear()
                data = self.client.get_status()
                if data is not None:
                    self.status, self.status_at = data, time.monotonic()
                last_poll = now
                self._apply_brightness()
            # repaint every second so running timers advance smoothly
            self.paint()
            time.sleep(1.0)


# =====================================================================
# PAGE DEFINITIONS  (15 keys, indices 0-14, 5 cols x 3 rows)
# =====================================================================
#   0  1  2  3  4
#   5  6  7  8  9
#  10 11 12 13 14
def build_pages(app_babies: Optional[list] = None) -> dict[str, dict[int, Key]]:

    # ---- HOME: nursery dashboard ------------------------------------
    def sleep_label(a):
        if not a.sleep_active():
            return "Sleep"
        return "End Night" if a.last("sleep").get("type") == "NIGHT_SLEEP" else "End Nap"
    def sleep_val(a):
        if a.sleep_active():
            return fmt_mins((a.last("sleep").get("minutesAgo") or 0) + a._elapsed() / 60)
        return None
    def sleep_bg(a): return BG_ACTIVE if a.sleep_active() else BG

    def pump_label(a): return "End Pump" if a.pump_active() else "Pump"
    def pump_bg(a): return BG_ACTIVE if a.pump_active() else BG

    def press_sleep(a):
        if a.sleep_active():
            a.log(2, {"type": "sleep", "action": "end"}, return_home=False)
        else:
            a.goto("sleep")   # pick type + location, commit there

    def press_pump(a):
        if a.pump_active():
            a.goto("pump")    # ask for amounts; that page ends the session
        else:
            a.pump_left = a.pump_right = 0.0
            a.log(3, {"type": "pump", "action": "start"}, return_home=False)

    # Overdue is the family's own threshold from GET /status, not our guess.
    def warn_bg(a, key: str):
        return BG_WARN if (a.status.get("warnings") or {}).get(key) else BG

    def count(a, k: str) -> str:
        v = a.daily(k)
        return "--" if v is None else str(v)

    home = {
        # row 1 — actions
        0: Key(icon="feed",   label="Feed",   on_press=lambda a: a.goto("feed")),
        1: Key(icon="diaper", label="Diaper", on_press=lambda a: a.goto("diaper")),
        2: Key(icon="sleep",  label_fn=sleep_label, value_fn=sleep_val,
               bg_fn=sleep_bg, on_press=press_sleep),
        3: Key(icon="pump",   label_fn=pump_label, bg_fn=pump_bg,
               value_fn=lambda a: fmt_mins(a.last("pump").get("minutesAgo"))
                                  if a.pump_active() else None,
               on_press=press_pump),
        4: Key(icon="bath",   label="Bath", on_press=lambda a: a.goto("bath")),

        # row 2 — how long ago (red when past the configured warning time)
        5: Key(icon="feed",   label="Last Feed",
               value_fn=lambda a: fmt_mins(a.last("feed").get("minutesAgo")),
               bg_fn=lambda a: warn_bg(a, "feedOverdue"),
               on_press=lambda a: a.goto("feed")),
        6: Key(icon="diaper", label="Last Dpr",
               value_fn=lambda a: fmt_mins(a.last("diaper").get("minutesAgo")),
               bg_fn=lambda a: warn_bg(a, "diaperOverdue"),
               on_press=lambda a: a.goto("diaper")),
        7: Key(icon="sleep",  label="Last Slp",
               value_fn=lambda a: fmt_mins(a.last("sleep").get("minutesAgo")),
               on_press=lambda a: a.goto("home")),
        8: Key(icon="pump",   label="Last Pump",
               value_fn=lambda a: fmt_mins(a.last("pump").get("minutesAgo"))),
        9: Key(icon="brand", label_fn=lambda a: a.baby_name(),
               on_press=lambda a: a.goto("babies") if len(a.babies) > 1 else None),

        # row 3 — today so far
        10: Key(icon="feed",   label="Feeds",
                value_fn=lambda a: count(a, "feeds"),
                on_press=lambda a: a.goto("feed")),
        11: Key(icon="diaper", label="Diapers",
                value_fn=lambda a: count(a, "diapers"),
                on_press=lambda a: a.goto("diaper")),
        12: Key(icon="sleep",  label="Sleep",
                value_fn=lambda a: fmt_mins(a.daily("sleepMinutes")),
                on_press=lambda a: a.goto("home")),
        13: Key(icon="settings", label="Bright",
                on_press=lambda a: (a.deck.set_brightness(BRIGHT_DAY), None)[1]),
        14: Key(icon="settings", label="Dim",
                on_press=lambda a: (a.deck.set_brightness(BRIGHT_NIGHT), None)[1]),
    }

    # ---- BATH sub-page: type + soap/shampoo, LOG commits ------------
    def set_bath_type(a, t):
        a.bath_type = t
        a.paint()

    def toggle_bath(a, attr):
        setattr(a, attr, not getattr(a, attr))
        a.paint()

    def press_log_bath(a):
        a.log(14, {"type": "bath", "bathType": a.bath_type,
                   "soapUsed": a.bath_soap, "shampooUsed": a.bath_shampoo})

    bath = {
        4: Key(label="Back", text_max=PICKER_TEXT,
               on_press=lambda a: a.goto("home")),
        # Independent on/off toggles, not a single-select group.
        5: Key(label="Soap", text_max=PICKER_TEXT,
               value_fn=lambda a: "Yes" if a.bath_soap else "No",
               bg_fn=lambda a: BG_SELECT if a.bath_soap else BG,
               on_press=lambda a: toggle_bath(a, "bath_soap")),
        6: Key(label="Shampoo", text_max=PICKER_TEXT,
               value_fn=lambda a: "Yes" if a.bath_shampoo else "No",
               bg_fn=lambda a: BG_SELECT if a.bath_shampoo else BG,
               on_press=lambda a: toggle_bath(a, "bath_shampoo")),
        14: Key(icon="bath", label="LOG", bg_fn=lambda a: BG_GO,
                on_press=press_log_bath),
    }
    for slot, (bath_label, api_value) in zip((0, 1, 2), BATH_TYPES):
        bath[slot] = Key(
            label=bath_label, text_max=PICKER_TEXT,
            bg_fn=lambda a, v=api_value: BG_SELECT if a.bath_type == v else BG,
            on_press=lambda a, v=api_value: set_bath_type(a, v))

    # ---- PUMP end screen: enter per-side amounts, then close the session ----
    # Amounts are stored per side; the API derives totalAmount and rejects any
    # attempt to set it directly, so both sides are entered here.
    def bump_pump(a, side, d):
        v = max(0.0, round(getattr(a, side) + d, 1))
        setattr(a, side, v)
        a.paint()

    def pump_total(a) -> float:
        return round(a.pump_left + a.pump_right, 1)

    def press_pump_save(a):
        payload = {"type": "pump", "action": "end", "unitAbbr": "OZ"}
        # Only send a side that was actually measured; 0 would record a real
        # zero, which is not the same as "not measured".
        if a.pump_left:
            payload["leftAmount"] = a.pump_left
        if a.pump_right:
            payload["rightAmount"] = a.pump_right
        a.log(14, payload)

    def press_pump_end_bare(a):
        a.log(10, {"type": "pump", "action": "end"})

    pump = {
        0: Key(label="L −", text_max=PICKER_TEXT,
               on_press=lambda a: bump_pump(a, "pump_left", -PUMP_STEP_OZ)),
        1: Key(label="Left", value_fn=lambda a: f"{a.pump_left:g}",
               text_max=PICKER_TEXT),
        2: Key(label="L +", text_max=PICKER_TEXT,
               on_press=lambda a: bump_pump(a, "pump_left", +PUMP_STEP_OZ)),
        4: Key(label="Back", text_max=PICKER_TEXT,
               on_press=lambda a: a.goto("home")),   # cancel: timer keeps running

        5: Key(label="R −", text_max=PICKER_TEXT,
               on_press=lambda a: bump_pump(a, "pump_right", -PUMP_STEP_OZ)),
        6: Key(label="Right", value_fn=lambda a: f"{a.pump_right:g}",
               text_max=PICKER_TEXT),
        7: Key(label="R +", text_max=PICKER_TEXT,
               on_press=lambda a: bump_pump(a, "pump_right", +PUMP_STEP_OZ)),

        10: Key(label="End no amt", text_max=13, on_press=press_pump_end_bare),
        12: Key(label="Total", value_fn=lambda a: f"{pump_total(a):g}oz",
                text_max=PICKER_TEXT),
        14: Key(icon="pump", label="SAVE", bg_fn=lambda a: BG_GO,
                on_press=press_pump_save),
    }

    # ---- FEED sub-page ----------------------------------------------
    def oz(a): return f"{a.feed_oz:g}oz"
    def bump(a, d):
        a.feed_oz = max(FEED_STEP_OZ, round(a.feed_oz + d, 1))
        a.paint()

    def breast_label(a, side):
        af = a.active_feed()
        if not af:
            return "Breast L" if side == "LEFT" else "Breast R"
        if af.get("activeSide") == side:
            return "End L" if side == "LEFT" else "End R"
        return "Switch L" if side == "LEFT" else "Switch R"

    def breast_val(a, side):
        """Per-side accrued time. leftDuration/rightDuration already include
        time accruing on the active side as of the last poll, so only the
        active side ticks locally between polls."""
        af = a.active_feed()
        if not af:
            return None
        base = af.get("leftDuration" if side == "LEFT" else "rightDuration", 0) or 0
        if af.get("activeSide") == side and not af.get("isPaused"):
            base += a._elapsed()
        return fmt_secs(base)

    def breast_bg(a, side):
        af = a.active_feed()
        if af and af.get("activeSide") == side:
            return BG_ACTIVE
        return BG

    def press_breast(a, idx, side):
        af = a.active_feed()
        if not af:
            a.log(idx, {"type": "feed", "feedType": "BREAST",
                        "action": "start", "side": side}, return_home=False)
        elif af.get("activeSide") == side:
            a.log(idx, {"type": "feed", "feedType": "BREAST", "action": "end"})
        else:
            a.log(idx, {"type": "feed", "feedType": "BREAST", "action": "switch"},
                  return_home=False)

    def log_bottle(a, idx, feed_type):
        a.log(idx, {"type": "feed", "feedType": feed_type,
                    "amount": a.feed_oz, "unitAbbr": "OZ"})

    feed = {
        0: Key(icon="feed",   label="Formula", value_fn=oz,
               on_press=lambda a: log_bottle(a, 0, "formula")),
        1: Key(icon="breast_l", label_fn=lambda a: breast_label(a, "LEFT"),
               value_fn=lambda a: breast_val(a, "LEFT"),
               bg_fn=lambda a: breast_bg(a, "LEFT"),
               on_press=lambda a: press_breast(a, 1, "LEFT")),
        2: Key(icon="breast_r", label_fn=lambda a: breast_label(a, "RIGHT"),
               value_fn=lambda a: breast_val(a, "RIGHT"),
               bg_fn=lambda a: breast_bg(a, "RIGHT"),
               on_press=lambda a: press_breast(a, 2, "RIGHT")),
        3: Key(icon="solids", label="Solids",
               on_press=lambda a: a.log(3, {"type": "feed", "feedType": "SOLIDS"})),
        4: Key(  label="Back", on_press=lambda a: a.goto("home")),

        5: Key(icon="feed",   label="Bottle BM", value_fn=oz,
               on_press=lambda a: log_bottle(a, 5, "breast milk")),
        6: Key(icon="feed",   label="Milk", value_fn=oz,
               on_press=lambda a: log_bottle(a, 6, "milk")),
        7: Key(label="– oz",  on_press=lambda a: bump(a, -FEED_STEP_OZ)),
        8: Key(label="+ oz",  on_press=lambda a: bump(a, +FEED_STEP_OZ)),
        9: Key(label_fn=oz),   # amount display
    }

    # ---- SLEEP sub-page: pick type + location, START commits ---------
    def set_sleep_type(a, t):
        a.sleep_type = t
        a.paint()

    def set_sleep_loc(a, loc):
        a.sleep_location = loc
        a.paint()

    def press_start_sleep(a):
        a.log(14, {"type": "sleep", "sleepType": a.sleep_type,
                   "action": "start", "location": a.sleep_location})

    sleep_page = {
        0: Key(label="Nap", text_max=PICKER_TEXT,
               bg_fn=lambda a: BG_SELECT if a.sleep_type == "NAP" else BG,
               on_press=lambda a: set_sleep_type(a, "NAP")),
        1: Key(label="Night", text_max=PICKER_TEXT,
               bg_fn=lambda a: BG_SELECT if a.sleep_type == "NIGHT_SLEEP" else BG,
               on_press=lambda a: set_sleep_type(a, "NIGHT_SLEEP")),
        4: Key(label="Back", text_max=PICKER_TEXT,
               on_press=lambda a: a.goto("home")),
        14: Key(icon="sleep", label="START",
                bg_fn=lambda a: BG_GO, on_press=press_start_sleep),
    }
    # Locations fill the second row and wrap onto the third.
    for slot, (loc_label, api_value) in zip((5, 6, 7, 8, 9, 10), SLEEP_LOCATIONS):
        sleep_page[slot] = Key(
            label=loc_label, text_max=PICKER_TEXT,
            bg_fn=lambda a, v=api_value: BG_SELECT if a.sleep_location == v else BG,
            on_press=lambda a, v=api_value: set_sleep_loc(a, v))

    # ---- DIAPER sub-page --------------------------------------------
    # Wet/Dirty/Both log immediately -- diaper is the highest-frequency action
    # and stays one press. Blowout/condition/colour are optional modifiers set
    # BEFORE the type key, and reset afterwards so the next press is plain.
    def toggle_blowout(a):
        a.diaper_blowout = not a.diaper_blowout
        a.paint()

    def pick(a, attr, value):
        # Pressing the selected value again clears it, back to unset.
        setattr(a, attr, None if getattr(a, attr) == value else value)
        a.paint()

    def press_diaper(a, idx, diaper_type):
        payload = {"type": "diaper", "diaperType": diaper_type,
                   "blowout": a.diaper_blowout}
        if a.diaper_condition:
            payload["condition"] = a.diaper_condition
        if a.diaper_color:
            payload["color"] = a.diaper_color
        a.log(idx, payload)
        a.diaper_blowout = False
        a.diaper_condition = a.diaper_color = None

    diaper = {
        0: Key(icon="diaper", label="Wet",
               on_press=lambda a: press_diaper(a, 0, "WET")),
        1: Key(icon="diaper", label="Dirty",
               on_press=lambda a: press_diaper(a, 1, "DIRTY")),
        2: Key(icon="diaper", label="Both",
               on_press=lambda a: press_diaper(a, 2, "BOTH")),
        3: Key(label="Blowout", text_max=13,
               bg_fn=lambda a: BG_WARN if a.diaper_blowout else BG,
               on_press=toggle_blowout),
        4: Key(label="Back", text_max=PICKER_TEXT,
               on_press=lambda a: a.goto("home")),
    }
    for slot, (lbl, value) in zip((5, 6, 7), DIAPER_CONDITIONS):
        diaper[slot] = Key(
            label=lbl, text_max=PICKER_TEXT,
            bg_fn=lambda a, v=value: BG_SELECT if a.diaper_condition == v else BG,
            on_press=lambda a, v=value: pick(a, "diaper_condition", v))
    for slot, (lbl, value) in zip((10, 11, 12, 13, 14), DIAPER_COLORS):
        diaper[slot] = Key(
            label=lbl, text_max=PICKER_TEXT,
            bg_fn=lambda a, v=value: BG_SELECT if a.diaper_color == v else BG,
            on_press=lambda a, v=value: pick(a, "diaper_color", v))

    # ---- BABY switcher: only reachable when the key sees more than one ----
    babies_page = {4: Key(label="Back", text_max=PICKER_TEXT,
                          on_press=lambda a: a.goto("home"))}
    for slot, baby in zip((0, 1, 2, 3, 5, 6, 7, 8, 9), (app_babies or [])):
        bid = baby.get("id")
        babies_page[slot] = Key(
            icon="brand", label=(baby.get("firstName") or "?"),
            text_max=PICKER_TEXT,
            bg_fn=lambda a, b=bid: BG_SELECT if a.client.baby_id == b else BG,
            on_press=lambda a, b=bid: a.switch_baby(b))

    return {"home": home, "feed": feed, "diaper": diaper,
            "sleep": sleep_page, "pump": pump, "bath": bath,
            "babies": babies_page}


# =====================================================================
# MAIN
# =====================================================================
def main():
    if "REPLACE" in API_KEY:
        print("!! Set ST_API_KEY (env or CONFIG block) first.\n")
    if "REPLACE" in BABY_ID:
        print("ST_BABY_ID not set; using the first baby from GET /babies.\n")
    app = App()
    app.open_deck()
    app.paint()
    try:
        app.run_loop()
    except KeyboardInterrupt:
        pass
    finally:
        if app.deck:
            app.deck.reset()
            app.deck.close()
        print("\nbye")


if __name__ == "__main__":
    main()
