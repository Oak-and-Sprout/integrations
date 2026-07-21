# CLAUDE.md — Nursery Deck

Guidance for AI agents working in this repo, whether helping someone set the
project up from scratch or making changes to it.

## What this is

A single-file Python daemon (`nursery_deck.py`, ~900 lines) that turns a
15-key Elgato Stream Deck on a Raspberry Pi into a tap-to-log baby care panel
for [Sprout Track](https://github.com/Oak-and-Sprout/sprout-track), via its
webhook API (`/api/hooks/v1`). It drives the deck raw over USB HID with
`python-elgato-streamdeck` — no Elgato desktop software, no plugin SDK.

There is no build system, no package manifest, and no test suite. Dependencies
are three pip packages (`streamdeck`, `pillow`, `requests`) plus system
libraries listed in the setup guide.

## Where things live

| Path | What it is |
|---|---|
| `nursery_deck.py` | The entire application |
| `docs/IMPLEMENTATION.md` | Step-by-step setup guide (Pi prep → systemd service), plus customization, troubleshooting, and a hardware-free render preview harness |
| `docs/webhook-api.md` | Reference for the Sprout Track webhook API this talks to — **read before writing any payload code**. May lag the [maintained upstream doc](https://github.com/Oak-and-Sprout/sprout-track/blob/main/documentation/Admin-Documentation/webhook-api.md), which it links at the top |
| `README.md` | Feature overview, page layouts, and the reasoning behind UX choices |
| `assets/` | 72×72-ready key icons (128px PNGs from Sprout Track's `public/`) |
| `example.env` | Config template — the canonical list of environment variables |

## Helping a user set this up

Follow `docs/IMPLEMENTATION.md` — it is ordered, complete, and every step that
exists because something failed the first time says so. Do not improvise an
alternative install; the non-obvious steps are load-bearing:

- **venv is mandatory** (PEP 668 blocks system pip). The systemd `ExecStart`
  must use `~/deck-env/bin/python`, not `/usr/bin/python3`.
- **`libhidapi-libusb0`** and Pillow's shared libraries (`libopenjp2-7`,
  `libfreetype6`, …) are apt packages the pip wheels do not bundle. Their
  absence produces errors that look like missing Python packages — the
  troubleshooting section (§13) decodes them.
- **udev rule** for vendor `0fd9` is required for non-root USB access.
- **`ST_BASE_URL` is the bare host.** The family slug is *not* part of the API
  path; including it returns an HTML 404 that looks like an auth failure.
- The API key comes from **Settings → Admin → Integrations**, needs read+write
  scope, and is shown only once at creation.
- Run in the foreground (§8) before installing the service, and verify a press
  actually appears in the Sprout Track timeline.

Configuration is entirely environment variables (`ST_BASE_URL`, `ST_API_KEY`,
`ST_BABY_ID`, `ST_TIMEZONE`, `ST_ICON_DIR`) read at the top of
`nursery_deck.py`. `ST_BABY_ID` is optional — the first baby from
`GET /babies` is used if unset.

## Architecture of nursery_deck.py

The file is organized top-to-bottom in banner-commented sections:

1. **CONFIG** — env vars and tunables (poll rate, dim schedule, oz steppers),
   plus the pinned value lists: `DIAPER_CONDITIONS`, `DIAPER_COLORS`,
   `SLEEP_LOCATIONS`, `BATH_TYPES`. Each list's comment explains why it is
   hardcoded (mostly: no `/reference` endpoint exists for it).
2. **ICONS / RENDERING** — `render_key()` composites a key face with Pillow:
   value badge on top, icon in the middle, auto-shrinking label at the bottom.
   Text-only keys render large centered text. `text_max` caps font size so a
   row of picker keys reads evenly.
3. **HooksClient** — thin API client. Reads are direct; writes go through an
   **offline outbox**: a background thread with exponential backoff (2s→60s,
   20 attempts). 4xx responses are permanent failures and are dropped, not
   retried; 429 and network errors retry.
4. **Key / App** — `Key` is a dataclass of optional callables (`label_fn`,
   `value_fn`, `bg_fn`, `on_press`) evaluated on every repaint. `App` owns the
   state machine: current page, picker selections, cached `GET /status`
   snapshot, and the paint loop.
5. **build_pages()** — every page is a plain `dict[int, Key]` keyed by
   position 0–14 (5 wide × 3 tall, left-to-right, top-to-bottom). This is
   where all layout and behavior lives.

Two loops: keypresses are event-driven callbacks; a background loop polls
`GET /status` every 30s and repaints once a second so timers tick. Repaints
are diffed on `(page, icon, label, value, bg)` — only changed keys are
re-rendered and re-uploaded, which is what keeps a Pi 2 at ~5% CPU. Any write
sets `_refresh` to force an immediate re-poll.

## Making changes

**Add or change a key/page:** edit the relevant dict in `build_pages()`. New
pages must be registered in the dict returned at the bottom of that function;
navigate with `a.goto("page_name")`. Follow the color conventions: `BG_SELECT`
(blue) = picked but not committed, `BG_ACTIVE` (green) = timer running,
`BG_GO` = the key that writes, `BG_WARN` = overdue.

**Change picker values:** edit the `(label, api_value)` lists in CONFIG. The
label is cosmetic; the second element is sent verbatim and — for sleep
locations — must exactly match the server's `/reference` list or the value is
silently not matched. Diaper condition/color values must stay `UPPER_CASE`
(see API trap #1 below).

**Preview rendering without hardware:** see `docs/IMPLEMENTATION.md` §12 — a
`FakeDeck` stub lets you render all 15 key faces to a PNG mosaic. Use this to
check label fit; overflow is obvious in an image and invisible in code review.

**Deploy:** the running copy lives on the Pi, not here. Edit locally, then:

```bash
scp nursery_deck.py USER@PI_HOST:~/nursery-deck/
ssh USER@PI_HOST 'sudo systemctl restart nursery-deck'
journalctl -u nursery-deck -f    # on the Pi, to watch it come up
```

**Verify writes by reading back**, not by status code (see traps below). The
cleanest check is the Sprout Track timeline or `GET /activities`.

## API traps — do not re-learn these

All established against a live instance; see `docs/webhook-api.md` for the API
reference and `docs/IMPLEMENTATION.md` §14 for these behaviors in context. The
ones that will silently corrupt data if forgotten:

1. **Unknown fields return 200 and are discarded.** A typo'd field name is
   invisible data loss. Never trust `{"success": true}` alone.
2. **Pump `totalAmount` is derived** from `leftAmount + rightAmount` and
   cannot be written. Always send per-side amounts.
3. **`condition`, `color`, `bathType`, `location`, `quality` are unvalidated
   free-form strings.** Wrong casing is stored verbatim and breaks the app's
   edit dropdowns (upstream issue #235). Use the exact values already pinned
   in CONFIG.
4. **Bath `soapUsed`/`shampooUsed` default to `true` when omitted** — always
   send them explicitly. Conversely, diaper `blowout` must be a literal
   boolean `true` to register.
5. **Zero ≠ absent, inconsistently.** Omit unmeasured amounts entirely; the
   existing pump/feed code shows the pattern (a side left at 0 is omitted).
6. **`caretakerName` misses silently** — an unknown name logs the activity
   unattributed with no error.
7. **4xx means the payload will never succeed.** The outbox already drops
   these; keep that behavior — retrying rejected payloads only burns the
   30 POST/min rate limit.

## Security

- The API key grants full read/write to a family's real data. It lives only
  in the env file on the Pi (`chmod 600`) and must never be committed or
  echoed into logs, commits, or docs. `.env` is gitignored; keep it that way.
- When experimenting against the API, use a throwaway family — failed write
  experiments persist in the real timeline.

## Scope decisions already made

Medicine, supplements, notes, measurements, play, and photos were deliberately
removed from the deck — they are better handled in the app, and cutting them
freed the Home page for live status. Before reinstating one, read the Scope
section of `README.md`: medicine/supplement logging requires restoring the
startup `GET /reference` fetch that resolved names.
