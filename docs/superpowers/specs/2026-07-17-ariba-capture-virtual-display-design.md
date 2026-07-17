# Ariba capture `--virtual-display` (#122, Part A)

## Problem

`capture_attachments` launches a **headed** Chromium — SAP Ariba blocks headless login, and the
whole flow was validated headed. On a headless server (plexbox, the nightly host) there is no X
display, so headed Chromium cannot start. To run the capture on the schedule the browser needs a
virtual framebuffer (Xvfb), driven via `pyvirtualdisplay` — exactly what the council /
Bid Award Panel scrapers already do behind `--virtual-display`.

## Design

Mirror the existing, in-repo pattern verbatim (`sources/bid_award_panel.py:agenda_fetcher`, wired
through `enrich-council --virtual-display` and `enrich-titles --virtual-display`):

- Add `virtual_display: bool = False` to `capture_attachments`. When true, start a
  `pyvirtualdisplay.Display(visible=False, size=(1440, 900))` before the `sync_playwright` block
  and `stop()` it in a `finally`, so the headed browser renders into the framebuffer. When false,
  behaviour is unchanged (a real display, as on a dev laptop).
- `pyvirtualdisplay` already ships with the `council` extra (added in #117) — no dependency
  change.
- Add a `--virtual-display` flag to `tb enrich-ariba-attachments`, threaded to
  `capture_attachments`. It is only meaningful with `--capture` (the only browser-driving mode);
  `--ingest`/`--reindex` are offline and ignore it.

## Testing

The browser half of this module is exercised live, not unit-tested (the module's own contract —
`login`/`capture_event`/`capture_attachments` need a real Ariba session). The `Display` wrapper is
part of that half. The one cheap, meaningful unit test is the **CLI wiring**: `tb
enrich-ariba-attachments --capture --virtual-display` calls `capture_attachments` with
`virtual_display=True` (patch `capture_attachments`, assert the kwarg) — the part a typo would
silently break. Offline, no browser.

## Out of scope (Part B — server deployment)

`sudo apt install xvfb` + Playwright system libs, `playwright install chromium`, the Ariba creds
in the server env, and the `tb-ariba-attachments` systemd timer. Those are the deployment steps
in #122, done on plexbox after this lands.

## References

- `sources/bid_award_panel.py:agenda_fetcher` — the `virtual_display` pattern being mirrored.
- `cli.py` — `enrich-council`/`enrich-titles` `--virtual-display` flags (the CLI precedent).
- #122 — the deployment this unblocks.
