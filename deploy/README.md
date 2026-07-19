# Deploying the nightly job

Design: [`docs/superpowers/specs/2026-07-17-deployment-design.md`](../docs/superpowers/specs/2026-07-17-deployment-design.md)

User-level systemd. The host has no passwordless sudo, and linger is already enabled for the
operator, so the only privileged step is installing two apt packages.

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

The **`GH_TOKEN`** is what `deploy/publish-data.sh` uses to upload the nightly export (see
**Publishing the export**, below). It
needs `repo` scope on `CivicTechTO/toronto-bids-data` (release write) and `workflow` scope on
`CivicTechTO/toronto-bids-frontend` (deploy dispatch). Append it to the same file:

```shell
read -rs T && printf 'GH_TOKEN=%s\n' "$T" >> ~/.config/toronto-bids/tb.env
chmod 600 ~/.config/toronto-bids/tb.env
```

Unlike the webhook, `GH_TOKEN` is **required** for publishing — the nightly's publish step fails
loudly without it (publishing is the deliverable, not a notification).

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

## Ariba document capture (opt-in, headed browser under Xvfb) — #122

`tb enrich-ariba-attachments --capture` archives the solicitation documents behind Ariba's
Respond gate (#117). It drives a **headed** Chromium (Ariba blocks headless login), so on this
headless box it runs under Xvfb via `--virtual-display`. It writes to the **same** `~/tb-data`
store, so the nightly's `tb export` includes the attachments. Its own timer, separate from
`tb-nightly` and well clear of it (noon vs 05:30).

Extra prerequisites beyond the base install:

```shell
# 1. Xvfb + Chromium's system libraries (the privileged step)
sudo apt install -y xvfb
cd ~/toronto-bids/scrapers
uv sync --extra council --locked        # playwright + pyvirtualdisplay + python-dotenv
uv run playwright install chromium      # the browser binary (~150MB, non-privileged)
sudo .venv/bin/python -m playwright install-deps chromium   # its shared libs (needs root)

# 2. Ariba credentials, appended to the same 0600 env file as the Slack webhook.
#    The account must NOT have MFA — an unattended login cannot answer a challenge.
# `read -r "U?prompt"` is bash-only and fails in zsh ("no coprocess"); prompt separately so
# this works in either shell.
umask 077
printf 'ARIBA_USERNAME: '; read -r U
printf 'ARIBA_PASSWORD: '; read -rs P; echo
printf 'ARIBA_USERNAME=%s\nARIBA_PASSWORD=%s\n' "$U" "$P" >> ~/.config/toronto-bids/tb.env
unset U P; chmod 600 ~/.config/toronto-bids/tb.env

# 3. Units + prove one run before scheduling.
cp ~/toronto-bids/deploy/tb-ariba-attachments.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user start tb-ariba-attachments.service
journalctl --user -u tb-ariba-attachments -n 50 --no-pager
systemctl --user enable --now tb-ariba-attachments.timer
```

The first run is a full sweep (~1-2h, ~44 open events); later runs are fast (a bundle already
on disk is skipped). Respond is disabled once a posting closes, so the capture only reaches
currently-open solicitations — this is why it runs daily, not once.

## What does NOT run here

`enrich-council` and `enrich-titles --scrape` need a **headed** Chromium (TMMIS is Akamai-gated
and blocks headless). They are not on the timer — but Playwright IS installed now (for the Ariba
capture above), so they can be run by hand under `--virtual-display` if ever needed:

- `enrich-titles --scrape` **will never find another agenda** — the Bid Award Panel was
  abolished on 2025-10-01 by By-law 766-2025, and the 891 cached pages are the final corpus.
- `enrich-council` covers 3 suspended firms and changes rarely.

Run them by hand if they are ever needed.
