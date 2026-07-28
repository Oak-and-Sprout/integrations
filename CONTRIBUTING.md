# Contributing

Thanks for sharing an integration. This repo is a community collection of things people wire up to [Sprout Track](https://github.com/Oak-and-Sprout/sprout-track). See the [README](README.md) for what belongs here and how the folders are laid out.

## How to add an integration

1. **Pick the group folder** (`elgato/`, `home-assistant/`, or a new one).
2. **Create a subfolder** for your integration. Use a clear name (kebab-case is preferred for new folders).
3. **Add a `README.md`** in that folder describing what it does, what you need, and how to set it up. Put full walkthroughs there (or in a `docs/` subfolder) when the deployment is non-trivial.
4. **Keep related files with the integration** (YAML, scripts, `assets/`, examples). Don't scatter them into a shared group-level tree.
5. **Link media** with relative paths from your README, e.g. `![Key layout](assets/key-layout.png)`.
6. **Open a pull request.** Mention the integration in the root README's Integrations section with a short blurb and a link to your folder.

If your integration doesn't fit an existing group, open an issue or PR proposing a new group folder rather than forcing it into one that doesn't fit.

Simple integrations can be a short README plus a few config files. Complex ones should keep deeper setup, API notes, and deployment detail in their own docs. The subfolder is the source of truth for how to run it.

## README template

Copy this into your integration's `README.md` to get started:

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
For complex deployments, link out to docs/IMPLEMENTATION.md (or similar)
and keep this section as a short overview.

## Notes & gotchas

Anything that tripped you up, limitations, or ideas for improvement.

## Media

![Description](assets/screenshot.png)
```

## Guidelines

- **Don't commit secrets.** Scrub API keys, tokens, webhook URLs, and family/child names from screenshots and code before you push.
- **Keep media reasonable.** Compress screenshots and trim video where you can. Large binaries bloat the repo for everyone.
- **Credit sources.** If you built on someone else's work, link it.
- **One integration per folder.** Split unrelated builds so people can find what they need.
- **Point deep docs at the subfolder.** If setup is long, don't paste it into the root README.

## License

This repo is licensed under the [MIT License](LICENSE). By submitting an integration, you agree to have it shared under the same terms.
