# Sprout Track Integrations

A community collection of integrations built on top of [Sprout Track](https://github.com/): its webhook/API, hardware companions, and anything else people wire up to log and automate care events around the house.

If you've hooked Sprout Track into Home Assistant, put it on a Stream Deck, or written a bit of glue code by hand, this is where you share it.

## What belongs here

Anything that connects Sprout Track to hardware or software the app doesn't ship with:

- **Home automation:** Home Assistant automations, dashboards, template sensors, webhook triggers
- **Hardware:** Stream Deck profiles, button boxes, tag readers, Raspberry Pi builds, anything physical that logs or displays care events
- **Hand-rolled code:** scripts, sidecars, bots, or small apps that talk to the Sprout Track API

Both polished projects and rough-but-working hacks are welcome. A write-up that saves someone else an afternoon is worth posting.

## How it's organized

Integrations are grouped by platform. Each group is a top-level folder with the write-ups inside it and a single `assets/` folder holding the media:

```
.
├── README.md
├── elgato/
│   ├── stream-deck-nursery-mode.md
│   └── assets/
│       └── stream-deck-nursery-mode-assets/
│           ├── key-layout.png
│           └── demo.mp4
└── home-assistant/
    ├── feeding-reminder-automation.md
    └── assets/
        └── feeding-reminder-automation-assets/
            └── dashboard.png
```

The two groups that exist today:

- **`elgato/`:** Stream Deck and other Elgato hardware
- **`home-assistant/`:** Home Assistant integrations

If your integration doesn't fit either, open an issue or PR proposing a new group folder rather than forcing it into an existing one.

## Submitting an integration

1. **Pick the group folder** your integration belongs in (`elgato/`, `home-assistant/`, or a new one).
2. **Write a markdown file** in that folder describing what you built and how to reproduce it. Give it a clear, kebab-case filename, e.g. `stream-deck-nursery-mode.md`.
3. **Create an assets folder** for your media inside that group's `assets/` directory. Name it after your markdown file's title plus `-assets`:
   - Write-up: `elgato/stream-deck-nursery-mode.md`
   - Its assets: `elgato/assets/stream-deck-nursery-mode-assets/`
4. **Reference your media** from the write-up using relative paths, e.g.
   `![Key layout](assets/stream-deck-nursery-mode-assets/key-layout.png)`
5. **Open a pull request.**

### Naming rule, in one line

> An integration's assets folder is always `<markdown-title>-assets`, and it always lives in that group's root `assets/` folder.

Screenshots, photos, and video all go in that one folder. No loose files at the group root.

## Write-up template

Copy this into your markdown file to get started:

```markdown
# <Integration Name>

**Platform:** Home Assistant / Elgato Stream Deck / custom
**Author:** <your name or handle>

## What it does

A sentence or two on the problem this solves.

## What you'll need

- Hardware / software / accounts required
- Sprout Track version or API features used

## Setup

Step-by-step instructions to reproduce it.

## Notes & gotchas

Anything that tripped you up, limitations, or ideas for improvement.

## Media

![Description](assets/<this-title>-assets/screenshot.png)
```

## Guidelines

- **Don't commit secrets.** Scrub API keys, tokens, webhook URLs, and family/child names from screenshots and code before you push.
- **Keep media reasonable.** Compress screenshots and trim video where you can. Large binaries bloat the repo for everyone.
- **Credit sources.** If you built on someone else's work, link it.
- **One integration per write-up.** Split unrelated builds into separate files so people can find what they need.

## License

This repo is licensed under the [MIT License](LICENSE). By submitting an integration, you agree to have it shared under the same terms.