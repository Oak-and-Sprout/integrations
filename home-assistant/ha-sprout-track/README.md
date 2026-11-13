# Sprout Track for Home Assistant (HACS integration)

**Platform:** Home Assistant
**Author:** [Boisti13](https://github.com/Boisti13)
**Repository:** https://github.com/Boisti13/ha-sprout-track (MIT)

## What it does

A custom integration that brings Sprout Track into Home Assistant: set up
in the UI with the server address and an API key, no YAML. Each baby
becomes a device with:

- **Sensors:** last feed, diaper, sleep, bath, medicine, supplement and
  pump (as times, so Home Assistant shows "2 hours ago"); today's counts
  of feeds, diapers (wet / dirty), sleep, naps, baths, medicines and
  supplements; age; latest weight, height, head circumference and
  temperature.
- **Per medicine and supplement:** when it was last given and, for
  medicines with a minimum time between doses, when the next dose is
  allowed.
- **Binary sensors:** sleeping, feed overdue, diaper overdue (using the
  warning times set in Sprout Track), breastfeeding, pumping.
- **Buttons:** log a wet, dirty or wet-and-dirty diaper, start a nap or
  night sleep, end sleep. Easy to put on a Zigbee button or a dashboard.
- **Actions:** `sprout_track.log_diaper`, `start_sleep`, `end_sleep`,
  `log_sleep`, `log_medicine`, `log_supplement` and `log_activity` for
  automations and scripts.

Babies and medicines added in Sprout Track appear by themselves. English
and German.

## What you'll need

- Sprout Track 1.6 or newer (the version with *Settings → Admin →
  Integrations*) and an API key with the scopes **read** and **write**
  (read alone gives the sensors without buttons and actions)
- Home Assistant 2025.8 or newer
- [HACS](https://hacs.xyz) (or copy the folder by hand)

## Setup

1. In Sprout Track: *Settings → Admin → Integrations*, create an API key
   (read + write) and copy it. It is shown only once.
2. In HACS: *⋮ → Custom repositories*, add
   `https://github.com/Boisti13/ha-sprout-track` as type *Integration*.
3. Search *Sprout Track* in HACS, download it, restart Home Assistant.
4. *Settings → Devices & services → Add integration → Sprout Track*:
   enter the server address (for example `http://192.168.1.50:3000`)
   and the key.

Entities, actions, examples and troubleshooting are in the
[repository's README](https://github.com/Boisti13/ha-sprout-track#readme).

## Notes & gotchas

- **Plain `http://` works only with an IP address** (or `localhost`).
  With a host name Sprout Track answers "HTTPS required": use the IP, or
  `https://` through a reverse proxy.
- **"Next dose" needs `doseMinTime` in the hooks API**, which released
  Sprout Track versions don't send yet
  ([#298](https://github.com/Oak-and-Sprout/sprout-track/issues/298)).
  Until then only "last given" appears per medicine.
- **API limits:** one update costs one read plus one per baby (default
  every 60 seconds); doses are only fetched when there is a new one.
  Give other tools their own key.
- "Today" starts at midnight in Home Assistant's time zone.
