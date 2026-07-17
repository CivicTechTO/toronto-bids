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

## 3. The Slack webhook

The repo is public. The webhook is a credential and lives only here, mode `0600`:

```shell
mkdir -p ~/.config/toronto-bids
read -rs W && printf 'TB_SLACK_WEBHOOK=%s\n' "$W" > ~/.config/toronto-bids/tb.env
chmod 600 ~/.config/toronto-bids/tb.env
```

Unset it and `tb nightly` still runs — it just posts nothing.

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
