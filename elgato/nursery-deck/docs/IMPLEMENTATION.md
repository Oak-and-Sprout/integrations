# Nursery Deck — Implementation Guide

Build a tap-to-log baby care panel from an Elgato Stream Deck and a Raspberry
Pi, talking to a [Sprout Track](https://github.com/Oak-and-Sprout/sprout-track) instance over its
webhook API.

The deck sits on the nursery dresser. Press Feed, Diaper, Sleep, Pump, or Bath
to log an event; the bottom rows show live status pulled from the API. No
Elgato desktop software is involved — the daemon drives the deck directly over
USB HID, so it runs headless on Linux.

Everything here was derived from an actual build. Where a step exists because
something failed the first time, that is called out.

## Contents

1. [What you need](#1-what-you-need)
2. [Prepare the Pi](#2-prepare-the-pi)
3. [Install system packages](#3-install-system-packages)
4. [Create the Python environment](#4-create-the-python-environment)
5. [Grant USB access without root](#5-grant-usb-access-without-root)
6. [Find your baby ID and confirm the API](#6-find-your-baby-id-and-confirm-the-api)
7. [Deploy the application](#7-deploy-the-application)
8. [First run, in the foreground](#8-first-run-in-the-foreground)
9. [Install the systemd service](#9-install-the-systemd-service)
10. [Day-to-day operation](#10-day-to-day-operation)
11. [Customizing](#11-customizing)
12. [Previewing key art without hardware](#12-previewing-key-art-without-hardware)
13. [Troubleshooting](#13-troubleshooting)
14. [API behavior worth knowing](#14-api-behavior-worth-knowing)
15. [Security notes](#15-security-notes)
16. [How it works, briefly](#16-how-it-works-briefly)

---

## 1. What you need

### Hardware

| Item | Notes |
|---|---|
| Raspberry Pi | Any model that runs Raspberry Pi OS. A Pi 2 (ARMv7, 1 GB) is sufficient — the daemon idles at roughly 5% CPU there. |
| Stream Deck, 15 keys | MK.2 or Original V2. Both enumerate identically and use the 5×3 layout this app assumes. |
| Network | Wired Ethernet is easiest for bring-up. Wi-Fi works. |

Other Stream Deck models (6-key Mini, 32-key XL) work with the underlying
library, but the page layouts here are written for 15 keys and would need
rework.

### Software

- Raspberry Pi OS **Lite** — no desktop needed. Match the architecture to your Pi
- Python 3.9 or newer (Raspberry Pi OS 13 ships 3.13).

### Sprout Track side

- A running Sprout Track instance you can reach from the Pi.
- An API key with **read and write** scopes. Create it in the main app under
  **Settings → Admin → Integrations** (not the Family Manager). Keys look like
  `st_live_…` and are shown **once** at creation — copy it immediately, it is
  stored as a hash and cannot be retrieved later.
- Optionally scope the key to a single baby.

---

## 2. Prepare the Pi

Flash Raspberry Pi OS Lite and enable SSH. In Raspberry Pi Imager, use the
gear icon to preset hostname, user, Wi-Fi, and SSH before writing the card.

Then confirm you can reach it and set the timezone — the API uses it to decide
which calendar day "today" means for daily counts:

```bash
ssh YOUR_USER@YOUR_PI_HOST
sudo timedatectl set-timezone America/Chicago    # use your own
```

Optionally set up key-based SSH from your workstation so deploys don't prompt:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_pi -N ''
ssh-copy-id -i ~/.ssh/id_ed25519_pi.pub YOUR_USER@YOUR_PI_HOST
```

---

## 3. Install system packages

```bash
sudo apt update
sudo apt install -y \
    python3-venv \
    libhidapi-libusb0 \
    libopenjp2-7 libtiff6 libwebp7 libfreetype6 libxcb1 \
    fonts-dejavu-core
```

Why each one matters:

- **`libhidapi-libusb0`** — the USB HID backend. Without it,
  `DeviceManager().enumerate()` raises *"no suitable LibUSB HIDAPI backend"*
  even though the Python package installed fine.
- **`libopenjp2-7`, `libtiff6`, `libwebp7`, `libfreetype6`, `libxcb1`** — Pillow
  ships as a prebuilt wheel that does **not** bundle its shared libraries. Pip
  reports success and then Pillow fails at import or at first font load. See
  troubleshooting below.
- **`fonts-dejavu-core`** — key labels. Without a TrueType font the renderer
  falls back to a tiny bitmap font.

Package names drift between releases. On Debian 13 (trixie) some are renamed
with a `t64` suffix from the 64-bit `time_t` transition — `libpng16-16` is now
`libpng16-16t64`, for instance. If a package is not found, search for it:

```bash
apt-cache --names-only search '^libpng16'
```

You do **not** need `libjpeg-dev`, `zlib1g-dev`, or `libusb-1.0-0-dev`. Those
are build dependencies, only relevant if pip has to compile Pillow from source
because no wheel exists for your platform.

---

## 4. Create the Python environment

Raspberry Pi OS marks system Python as externally managed (PEP 668), so
`pip install` into it is refused. Use a virtualenv:

```bash
python3 -m venv ~/deck-env
~/deck-env/bin/pip install --upgrade pip
~/deck-env/bin/pip install streamdeck pillow requests
```

Remember the interpreter path — the systemd unit must use
`~/deck-env/bin/python`, not `/usr/bin/python3`.

Verify the deck is visible before going further:

```bash
lsusb | grep -i elgato
~/deck-env/bin/python -c "
from StreamDeck.DeviceManager import DeviceManager
print([d.deck_type() for d in DeviceManager().enumerate()])"
```

Expect something like `['Stream Deck Original']` or `['Stream Deck MK.2']`. An
empty list at this stage usually means permissions — continue to the next step.

---

## 5. Grant USB access without root

By default only root can open the device. Add a udev rule for Elgato's vendor
ID (`0fd9`):

```bash
sudo tee /etc/udev/rules.d/10-streamdeck.rules >/dev/null <<'EOF'
SUBSYSTEM=="usb", ATTRS{idVendor}=="0fd9", MODE="0660", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0fd9", MODE="0660", GROUP="plugdev", TAG+="uaccess"
EOF

sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=0fd9
```

Confirm your user is in `plugdev` (the default Raspberry Pi OS user is):

```bash
groups | tr ' ' '\n' | grep plugdev || sudo usermod -aG plugdev "$USER"
```

`udevadm trigger` normally applies the rule to an already-connected deck. If
enumeration still fails, unplug and replug it. If you had to add yourself to
`plugdev`, log out and back in.

---

## 6. Find your baby ID and confirm the API

The API base path is:

```
{BASE_URL}/api/hooks/v1
```

**The family slug is not part of the API path.** If you reach your family at
`https://example.com/your-family-slug`, the API base is still
`https://example.com` — the key is already scoped to a family. Including the
slug returns a 404 HTML page rather than a JSON error, which is easy to
misread as an auth problem.

List babies to get the ID:

```bash
curl -s -H "Authorization: Bearer st_live_YOUR_KEY" \
  https://YOUR_SPROUT_HOST/api/hooks/v1/babies
```

```json
{"success":true,"data":{"babies":[{"id":"xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
 "firstName":"...","ageInDays":123}]}}
```

Copy that `id`. Confirm read access works end to end:

```bash
curl -s -H "Authorization: Bearer st_live_YOUR_KEY" \
  "https://YOUR_SPROUT_HOST/api/hooks/v1/babies/YOUR_BABY_ID/status?timezone=America/Chicago"
```

**On HTTPS:** requests from public networks must use HTTPS. Plain HTTP is
allowed only from loopback and RFC 1918 private ranges — fine if your instance
is on the same LAN as the Pi.

---

## 7. Deploy the application

From your workstation:

```bash
ssh YOUR_USER@YOUR_PI_HOST 'mkdir -p ~/nursery-deck/assets'
scp nursery_deck.py YOUR_USER@YOUR_PI_HOST:~/nursery-deck/
scp assets/*.png    YOUR_USER@YOUR_PI_HOST:~/nursery-deck/assets/
```

The icons are the `-128` PNGs from Sprout Track's `public/` folder. Key faces
render at 72×72, so the 128px variants downscale cleanly.

Create the config file **on the Pi** (keeping the key off your workstation's
disk and out of git):

```bash
cat > ~/nursery-deck/nursery-deck.env <<'EOF'
ST_BASE_URL=https://YOUR_SPROUT_HOST
ST_API_KEY=st_live_YOUR_KEY
ST_BABY_ID=YOUR_BABY_ID
ST_TIMEZONE=America/Chicago
ST_ICON_DIR=/home/YOUR_USER/nursery-deck/assets
EOF
chmod 600 ~/nursery-deck/nursery-deck.env
```

---

## 8. First run, in the foreground

Run it directly before installing the service, so errors are visible:

```bash
cd ~/nursery-deck
set -a && . ./nursery-deck.env && set +a
~/deck-env/bin/python -u nursery_deck.py
```

Expected:

```
Connected: Stream Deck Original (15 keys)
```

The keys should light up within a second or two. Press one and confirm the
event appears in the Sprout Track timeline. `Ctrl-C` to stop.

Use `-u` (unbuffered). Without it, output is buffered and a crash can appear
as silence.

---

## 9. Install the systemd service

```bash
sudo tee /etc/systemd/system/nursery-deck.service >/dev/null <<EOF
[Unit]
Description=Sprout Track Nursery Deck
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
SupplementaryGroups=plugdev
WorkingDirectory=/home/$USER/nursery-deck
EnvironmentFile=/home/$USER/nursery-deck/nursery-deck.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/$USER/deck-env/bin/python /home/$USER/nursery-deck/nursery_deck.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now nursery-deck
```

Points that matter:

- `ExecStart` uses the **venv** interpreter. `/usr/bin/python3` cannot see the
  installed packages.
- `PYTHONUNBUFFERED=1` makes logs appear in the journal promptly.
- `Restart=always` covers the deck being unplugged: the process exits and
  systemd retries every 5 seconds until it reappears.

Verify, including across a reboot — a cold boot is where service ordering
problems actually show up:

```bash
systemctl is-active nursery-deck
systemctl is-enabled nursery-deck
journalctl -u nursery-deck -f

sudo reboot
# after it comes back:
systemctl is-active nursery-deck
```

---

## 10. Day-to-day operation

```bash
sudo systemctl restart nursery-deck
sudo systemctl stop nursery-deck
journalctl -u nursery-deck -n 50 --no-pager
journalctl -u nursery-deck -f
```

To deploy a change:

```bash
scp nursery_deck.py YOUR_USER@YOUR_PI_HOST:~/nursery-deck/
ssh YOUR_USER@YOUR_PI_HOST 'sudo systemctl restart nursery-deck'
```

---

## 11. Customizing

All of these are constants near the top of `nursery_deck.py`.

**Poll rate and brightness**

```python
POLL_SECONDS      = 30    # GET /status cadence; API allows 60 GET/min
AUTO_HOME_SECONDS = 2.0   # return to home after a successful log
DIM_START_HOUR    = 21    # dim at 9pm
DIM_END_HOUR      = 7     # brighten at 7am
BRIGHT_DAY        = 70
BRIGHT_NIGHT      = 15
```

**Sleep locations** — `SLEEP_LOCATIONS` maps a short key label to the exact
value the API expects. Discover valid values for your family:

```bash
curl -s -H "Authorization: Bearer st_live_YOUR_KEY" \
  "https://YOUR_SPROUT_HOST/api/hooks/v1/babies/YOUR_BABY_ID/reference?type=sleep-locations"
```

The label is cosmetic; the second element must match the server's list exactly
or the location is not recorded.

**Bath types** — `BATH_TYPES`. There is no reference endpoint for these
(`bathType` is a free-form string), so the list mirrors the app's own dropdown.
Add custom values you already use in the app.

**Colors** — `BG_SELECT` (blue) marks a picker choice that has not been
committed; `BG_ACTIVE` (green) means a timer is genuinely running; `BG_GO` is
the key that writes; `BG_WARN` is an overdue warning. Keeping "selected" and
"running" visually distinct matters more than it sounds at 3am.

**Layout** — pages are plain dicts keyed by index, 0–14, left to right, top to
bottom:

```
 0  1  2  3  4
 5  6  7  8  9
10 11 12 13 14
```

Each `Key` takes an icon, a static label, and optional callables evaluated on
every repaint: `label_fn`, `value_fn`, `bg_fn`, plus `on_press`. Add a page by
adding a dict and registering it in the return of `build_pages()`; navigate to
it with `a.goto("your_page")`.

---

## 12. Previewing key art without hardware

Rendering can be exercised off-device, which is far faster than redeploying to
see whether a label fits. Substitute the two PIL helpers and pass any object
with a `key_image_format()`:

```python
import nursery_deck as nd
from PIL import Image

KEY = 72
class FakeDeck:
    def key_image_format(self):
        return {"size": (KEY, KEY), "format": "JPEG",
                "flip": (False, False), "rotation": 0}

nd.PILHelper.create_image = lambda deck, background=(0, 0, 0): \
    Image.new("RGB", (KEY, KEY), background)
nd.PILHelper.to_native_format = lambda deck, image: image

pages = nd.build_pages()
# then call nd.render_key(FakeDeck(), icon, label, value, bg, text_max)
# for each key and paste the results into one image
```

Composite the 15 results into a 5×3 mosaic and open it. Long labels overflowing
their key, or clipped descenders, are obvious in an image and invisible in code
review.

---

## 13. Troubleshooting

**`Missing dependency` or `ImportError` naming a `.so` file**

```
ImportError: libopenjp2.so.7: cannot open shared object file
ImportError: libfreetype.so.6: cannot open shared object file
```

Pillow's wheel does not bundle its shared libraries. Install the package that
provides the named library and retry. `libopenjp2.so.7` fails at
`import PIL.Image`; `libfreetype.so.6` fails later, at the first
`ImageFont.truetype()` call — so a program can start and then die on its first
render. Broad `except ImportError` handlers commonly mask these as "package not
installed"; read the underlying exception.

**`Probe failed` / no HID backend**

```
No suitable LibUSB HIDAPI backend was found
```

`libhidapi-libusb0` is missing.

**`enumerate()` returns an empty list**

Check `lsusb | grep -i elgato`. If the device is listed, it is a permissions
problem: confirm the udev rule, that you are in `plugdev`, and replug. If the
device is *not* listed, suspect power — use a powered hub.

**Keys light up, then the deck goes dark or drops out**

Almost always power. Use a powered hub.

**404 HTML instead of JSON**

You included the family slug in the API path. Use the bare host — see step 6.

**`INSUFFICIENT_SCOPE`**

The key is read-only. Recreate it with write scope.

**Writes appear to succeed but nothing shows up**

Check the response body, not just the status code. Unrecognized fields are
ignored silently — see the next section.

**Daily counts look wrong / reset at odd times**

`ST_TIMEZONE` is missing or wrong. Without it the server's local timezone
decides which day a record belongs to.

---

## 14. API behavior worth knowing

Established by testing against a live instance. All of these return HTTP 200.

**Unknown fields are silently discarded.** Posting a pump with a single
`amount` field returns success and stores nothing — the record ends up with
null amounts. There is no warning. Verify by reading the record back, not by
checking the status code.

**Some fields are derived and cannot be written.** Pump `totalAmount` is
computed from `leftAmount + rightAmount`. Sending `totalAmount` directly is
ignored. To record a total you must populate the per-side fields.

**Booleans that default to true must be sent explicitly.** Bath `soapUsed` and
`shampooUsed` default to `true` when omitted, so "no soap" requires an explicit
`false`.

**Zero is not the same as absent.** Sending `0` for an amount records a real
zero, which is a different fact from "not measured". Omit the field instead.

**Reference lists can contain duplicates.** One instance returned both
`Car Seat` and `CAR_SEAT` in `sleepLocations` — presumably a legacy enum value
alongside the display string. Inspect your own list before wiring it to keys.

**Name-matched fields must match exactly.** `medicineName`, `supplementName`,
and sleep `location` are matched against configured values. Send the canonical
string from `/reference`, not an abbreviated key label.

**Rate limits** are 60 GET/min and 30 POST/min per key. Human press rates never
approach the POST limit. A 30-second status poll uses 2 of 60.

**Breastfeed and sleep timers are shared state.** A session started via the API
is live in the app and can be ended there, and vice versa. Timer actions are
`start`, `switch`, `pause`, `resume`, `end`.

---

## 15. Security notes

- The API key grants full read/write to your family's data. Treat it as a
  password.
- Keep it in the env file on the Pi with `chmod 600`. Do not commit it. Add
  `.env` to `.gitignore` **before** the first commit — a `.gitignore` added
  later does not untrack a file already committed, and the value stays in git
  history.
- If a key is exposed, revoke and reissue it in **Settings → Admin →
  Integrations**. Rotating is more reliable than trying to scrub history.
- Prefer a baby-scoped key if the deck only ever logs for one child.
- If you experiment against the API, use a throwaway family rather than live
  data. Several of the behaviors in section 14 can only be discovered by
  writing records, and the failed attempts persist in the timeline.

---

## 16. How it works, briefly

**Two loops.**

*Input* is event-driven: a keypress fires a callback that queues a POST.

*Refresh* polls `GET /status` every 30 seconds. Between polls, running timers
tick locally from the cached session start and accrued durations, reconciling
on the next poll. The result is a smooth per-second display at near-zero API
cost. A write triggers an immediate re-poll so a toggled key reflects its new
state without waiting out the cycle.

**Offline outbox.** Writes go through a queue with exponential backoff (2s to
60s), so a press during a Wi-Fi blip still lands. Responses in the 4xx range
are treated as permanent and dropped rather than retried — a rejected payload
will never succeed, and retrying only consumes the rate limit.

**Incremental repaint.** The loop repaints every second so timers tick, but
each key is diffed on its inputs and only re-rendered and re-uploaded when its
face actually changes. On a Pi 2 this is the difference between roughly 24%
and 5% sustained CPU.

**Pages are the daemon's own state machine.** The Elgato plugin SDK only runs
inside Elgato's desktop app (Windows and macOS), so it is unusable here.
Pressing a category repaints all 15 keys from a different dict. This is why the
project can run headless on Linux at all.
