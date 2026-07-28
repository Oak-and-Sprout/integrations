# Sprout Track Integrations

A community collection of integrations built on top of [Sprout Track](https://github.com/Oak-and-Sprout/sprout-track): its webhook/API, hardware companions, and anything else people wire up to log and automate care events around the house.

If you've hooked Sprout Track into Home Assistant, setup a push button box, put it on a Stream Deck, or written a bit of glue code by hand, this is where you share it.

## Integrations

### Elgato

- **[Nursery Deck](elgato/nursery-deck/):** Stream Deck on a Pi that logs care events to Sprout Track over the webhook API. Built as a dedicated nursery panel with live status; full setup lives in that folder.

### Home Assistant

- **[Voice Control](home-assistant/Home%20Assistant%20Voice%20Control/):** Voice-driven Sprout Track actions via a Home Assistant automation and `rest_command`.
- **[Signal Overdue Notifications](home-assistant/Home%20Assistant%20Signal%20Overdue%20Notifications/):** Notifies a Signal group when feed or diaper is overdue. Works with other Home Assistant notify targets too.

## What belongs here

Anything that connects Sprout Track to hardware or software the app doesn't ship with:

- **Home automation:** Home Assistant automations, dashboards, template sensors, webhook triggers
- **Hardware:** Stream Deck profiles, button boxes, tag readers, Raspberry Pi builds, anything physical that logs or displays care events
- **Hand-rolled code:** scripts, sidecars, bots, or small apps that talk to the Sprout Track API

Both polished projects and rough-but-working hacks are welcome. A write-up that saves someone else an afternoon is worth posting.

## How it's organized

Integrations are grouped by platform under a top-level folder. Each integration is its **own subfolder** with a `README.md` and whatever files it needs (YAML, scripts, assets, docs).

```
.
├── README.md
├── CONTRIBUTING.md
├── elgato/
│   └── nursery-deck/          # complex: code, assets, docs/IMPLEMENTATION.md
│       ├── README.md
│       ├── docs/
│       └── ...
└── home-assistant/
    ├── Home Assistant Voice Control/
    │   ├── README.md
    │   └── *.yaml
    └── Home Assistant Signal Overdue Notifications/
        ├── README.md
        └── *.yaml
```

Platform groups today:

- **`elgato/`:** Stream Deck and other Elgato hardware
- **`home-assistant/`:** Home Assistant integrations

Simple integrations can be a short README plus a few config files. Complex ones (like Nursery Deck) keep deeper setup, API notes, and deployment detail in their own docs. The root README only catalogs them; the subfolder is the source of truth for how to run it.

If your integration doesn't fit an existing group, open an issue or PR proposing a new group folder rather than forcing it into one that doesn't fit.

## Contributing

See **[CONTRIBUTING.md](CONTRIBUTING.md)** for how to add an integration, the README template, and guidelines.

## License

This repo is licensed under the [MIT License](LICENSE). By submitting an integration, you agree to have it shared under the same terms.
