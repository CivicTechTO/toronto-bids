# Deploying the nightly job

Design: [`docs/superpowers/specs/2026-07-17-deployment-design.md`](../docs/superpowers/specs/2026-07-17-deployment-design.md)

User-level systemd. `tb nightly` is a single unified run: sync, award summaries, Ariba
attachment capture, agency (Zoo/TRCA/EP) board-report scrapes, council enrichment (monthly,
gated to the 1st), export, then publish. The browser-driven captures run in-line, isolated the
same way sources are — a failure in one is recorded and never blocks the rest, and export +
publish still run.

The host has no passwordless sudo, and linger is already enabled for the operator, so the only
privileged steps are installing a handful of apt packages.

## 1. Prerequisites (the only step needing a password)

```shell
sudo apt install -y poppler-utils pipx
pipx install uv
```

`poppler-utils` is required: `download_pdf` shells out to `pdftotext`, and a missing binary
raises `FileNotFoundError` per form — `download_award_summaries` catches it, logs `skipped`,
never writes the row, and therefore **re-downloads the same form on every run, forever**.

`pipx` rather than uv's `curl … | sh` installer: distro packages over piping a remote script
into a shell.

### Browser prerequisites (now required — the nightly drives a headed browser)

`tb enrich-ariba-attachments --capture` (#117) archives the solicitation documents behind
Ariba's Respond gate, and the agency board-report scrapes (#135) share the same headed-Chromium
prober. Both now run in-line inside `tb nightly`, so their prerequisites are required, not
optional:

```shell
# 1. Xvfb + Chromium's system libraries, and zip (the publish step builds
#    council-agendas.zip) — the privileged step
sudo apt install -y xvfb zip
cd ~/toronto-bids/scrapers
uv sync --extra council --locked        # playwright + pyvirtualdisplay + python-dotenv
uv run playwright install chromium      # the browser binary (~150MB, non-privileged)
sudo .venv/bin/python -m playwright install-deps chromium   # its shared libs (needs root)

# 2. Ariba credentials, appended to the same 0600 env file as the Slack webhook (see §3 below).
#    The account must NOT have MFA — an unattended login cannot answer a challenge.
# `read -r "U?prompt"` is bash-only and fails in zsh ("no coprocess"); prompt separately so
# this works in either shell.
umask 077
printf 'ARIBA_USERNAME: '; read -r U
printf 'ARIBA_PASSWORD: '; read -rs P; echo
printf 'ARIBA_USERNAME=%s\nARIBA_PASSWORD=%s\n' "$U" "$P" >> ~/.config/toronto-bids/tb.env
unset U P; chmod 600 ~/.config/toronto-bids/tb.env
```

The Ariba capture's first run is a full sweep (~1-2h, ~44 open events); later runs are fast (a
bundle already on disk is skipped). Respond is disabled once a posting closes, so the capture
only reaches currently-open solicitations — this is why it runs nightly, not once.

## 2. Code

```shell
git clone https://github.com/CivicTechTO/toronto-bids.git ~/toronto-bids
cd ~/toronto-bids/scrapers && uv sync --locked
uv run tb --version
```

## 3. Credentials — the Slack webhook and the publish token

The repo is public. Both are credentials and live only here, mode `0600`:

```shell
mkdir -p ~/.config/toronto-bids
umask 077
read -rs W && printf 'TB_SLACK_WEBHOOK=%s\n' "$W" > ~/.config/toronto-bids/tb.env
chmod 600 ~/.config/toronto-bids/tb.env
```

Unset the webhook and `tb nightly` still runs — it just posts nothing.

**Publishing** (see **Publishing the export**, below) needs `gh` to be **authenticated** with
`repo` scope on `CivicTechTO/toronto-bids-data` (release write) and `workflow` scope on
`CivicTechTO/toronto-bids-frontend` (deploy dispatch). Either works:

- **A prior `gh auth login` on the box** (check with `gh auth status`) — nothing to add here; or
- **A `GH_TOKEN`** appended to the same env file, for an unattended service token:

  ```shell
  read -rs T && printf 'GH_TOKEN=%s\n' "$T" >> ~/.config/toronto-bids/tb.env
  chmod 600 ~/.config/toronto-bids/tb.env
  ```

Unlike the webhook, authentication is **required** for publishing — the nightly's publish step
fails loudly if `gh auth status` fails (publishing is the deliverable, not a notification).

## 4. Units

```shell
mkdir -p ~/.config/systemd/user
cp ~/toronto-bids/deploy/tb-nightly.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
```

## 5. Prove it before scheduling it

```shell
systemctl --user start tb-nightly.service
journalctl --user -u tb-nightly -n 50 --no-pager
```

Then enable the timer:

```shell
systemctl --user enable --now tb-nightly.timer
systemctl --user list-timers tb-nightly.timer
```

## Operating

| what | command |
|---|---|
| last run | `journalctl --user -u tb-nightly -n 50 --no-pager` |
| next fire | `systemctl --user list-timers tb-nightly.timer` |
| run now | `systemctl --user start tb-nightly.service` |
| row counts / per-source status | `cd ~/toronto-bids/scrapers && TB_DATA_DIR=~/tb-data uv run tb status` |
| update | `cd ~/toronto-bids && git pull && cd scrapers && uv sync --locked` |

Updating is deliberately manual.

## Publishing the export — #146

Design: [`docs/superpowers/specs/2026-07-19-publish-data-design.md`](../docs/superpowers/specs/2026-07-19-publish-data-design.md)

The nightly's `ExecStart` is `deploy/tb-nightly-run.sh`, which runs `tb nightly` and then
`deploy/publish-data.sh`. Publish runs **even after a partial sync** (the export is still valid),
and the unit fails if **either** step failed — neither masks the other.

`publish-data.sh` uploads `bids.json`, `bids.json.gz`, and `bids.sqlite` to a rolling **`latest`**
release on `CivicTechTO/toronto-bids-data`, giving the frontend a stable URL:

```
https://github.com/CivicTechTO/toronto-bids-data/releases/download/latest/bids.json
```

On the 1st of each month it also cuts a dated `snapshot-YYYY-MM-DD` release (point-in-time
citation), then triggers the frontend build (`gh workflow run deploy.yml`, best-effort).

The nightly posts two Slack messages: `tb nightly` posts the rich archive report first, then
`publish-data.sh` posts a one-line publish result (✅ + release URL, or ❌ + reason). Both use
`TB_SLACK_WEBHOOK` and both no-op silently when it is unset.

**One-time prerequisites** (do these before the first publish):

```shell
sudo apt install -y gh              # the GitHub CLI (privileged, once)
# Create the data repo (public). Do this once, from any authenticated machine:
gh repo create CivicTechTO/toronto-bids-data --public \
  --description "Nightly export of the Toronto procurement archive (data only)."
```

Then add `GH_TOKEN` to `~/.config/toronto-bids/tb.env` (§3).

**Self-test before wiring it live** — prints every `gh` command instead of running it, after the
real artifact/JSON checks and gzip (needs a prior `tb export`, no token, no data repo):

```shell
cd ~/toronto-bids
TB_PUBLISH_DRY_RUN=1 TB_DATA_DIR=~/tb-data deploy/publish-data.sh
# Force the 1st-of-month snapshot path on any day:
TB_PUBLISH_DRY_RUN=1 TB_PUBLISH_DAY=01 TB_DATA_DIR=~/tb-data deploy/publish-data.sh
```

### Provisioning a fresh box from the archives

The DB itself is published (`bids.sqlite` on the `latest` release), so a new box can start from
it directly. To rebuild the *derived* Bid Award Panel data from primary sources instead (the
`enrich-titles --scrape` path was retired — the Panel is abolished and the corpus is final),
download the agenda archive and re-derive offline:

```shell
gh release download council-agendas -R CivicTechTO/toronto-bids-data -D /tmp/agendas
mkdir -p ~/tb-data/council/agendas
unzip -q -o /tmp/agendas/council-agendas.zip -d ~/tb-data/council/agendas
cd ~/toronto-bids/scrapers && TB_DATA_DIR=~/tb-data uv run tb enrich-titles   # offline, no browser
```

### R2 mirror of bids.sqlite — for browser-side Datasette-Lite (#155)

GitHub release assets no longer send `Access-Control-Allow-Origin`, so the frontend's in-browser
SQL page cannot load `bids.sqlite` from the release. `publish-data.sh` therefore also mirrors
`bids.sqlite` each night to a **CORS-enabled Cloudflare R2 bucket** (overwriting the same object,
so the URL is stable), reusing `CLOUDFLARE_API_TOKEN` via `wrangler`. The step is **best-effort**
(a failed push warns; the GitHub release is the deliverable) and **skips cleanly** when
`CLOUDFLARE_API_TOKEN` is unset.

Public URL: `https://pub-99a890c186c743c19ef7bcd00024dca8.r2.dev/bids.sqlite`
(Datasette-Lite: `https://lite.datasette.io/?url=<that URL>`).

**One-time R2 setup** (already done for the current deployment):

```shell
# 1. Enable R2 in the Cloudflare dashboard (accept terms; free tier covers this).
# 2. Create a scoped API token (Workers R2 Storage: Edit) + note the Account ID; add to tb.env:
#      CLOUDFLARE_API_TOKEN=...      (0600, gitignored — same file as the Slack webhook)
#      CLOUDFLARE_ACCOUNT_ID=...
# 3. Provision the bucket (wrangler runs via npx — no global install):
wr() { ( set -a; . ~/.config/toronto-bids/tb.env; set +a; npx -y wrangler@latest "$@" ); }
wr r2 bucket create toronto-bids-data
wr r2 bucket dev-url enable toronto-bids-data          # -> the public pub-*.r2.dev URL
# CORS must allow '*' (NOT the frontend origin — Datasette-Lite fetches from lite.datasette.io):
printf '{"rules":[{"allowed":{"origins":["*"],"methods":["GET","HEAD"],"headers":["*"]},"maxAgeSeconds":3600}]}' > /tmp/r2-cors.json
wr r2 bucket cors set toronto-bids-data --file /tmp/r2-cors.json
```

`TB_R2_BUCKET` overrides the bucket name (default `toronto-bids-data`).

## What does NOT run here

`enrich-council` needs a **headed** Chromium (TMMIS is Akamai-gated and blocks headless), but
Playwright is installed as part of the base setup now (§1, "Browser prerequisites") — it runs
monthly inside the nightly (gated to the 1st), so there is nothing separate to schedule for it.

- The Bid Award Panel title-recovery scrape **will never find another agenda** — the Panel was
  abolished on 2025-10-01 by By-law 766-2025, and the 891 cached pages are the final corpus.
- `enrich-council` covers 3 suspended firms and changes rarely; it runs monthly inside the
  nightly (gated to the 1st) rather than every run.

Run either by hand if ever needed outside the nightly's own schedule.
