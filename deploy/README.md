# Deploying the nightly job

The nightly pipeline runs under user-level systemd. `tb nightly` runs each step in sequence:

`sync -> award summaries -> portal -> bonfire listings -> MERX -> Ariba attachments -> agencies -> waterfront awards -> council (1st of month only) -> supplier rebuild -> export`

Each step is isolated: a failure in one step is recorded in `sync_run` and does not stop later steps, export, or publishing. The systemd wrapper `deploy/tb-nightly-run.sh` then runs `deploy/publish-data.sh` regardless of `tb nightly`'s exit code, and exits non-zero if either step failed.

Because the host has no passwordless `sudo` and systemd linger is already enabled, only system-package installation requires a password.

## 1. Prerequisites

```shell
sudo apt install -y poppler-utils pipx xvfb zip
pipx install uv
```

- `poppler-utils` provides `pdftotext` (required for PDF downloads; without it, `download_award_summaries` logs `skipped`, never records the row, and re-downloads the same files on every run).
- `pipx` installs `uv` from distro packages instead of piping a remote shell script.
- `xvfb` and `zip` are needed for headed-Chromium captures (Ariba attachments, agency board reports, council enrichment) and building `council-agendas.zip`.

Install the browser binary and its shared libraries:

```shell
cd ~/toronto-bids/scrapers
uv sync --locked
uv run playwright install chromium
sudo .venv/bin/python -m playwright install-deps chromium
```

`playwright`, `pyvirtualdisplay`, and `python-dotenv` are base dependencies installed by `uv sync --locked`.

## 2. Clone and verify

```shell
git clone https://github.com/CivicTechTO/toronto-bids.git ~/toronto-bids
cd ~/toronto-bids/scrapers && uv sync --locked
uv run tb --version
```

## 3. Credentials

This repository is public. Store all secrets in `~/.config/toronto-bids/tb.env` with mode `0600` (see `scrapers/.env.example` for local variable names).

### Slack webhook (optional)

```shell
mkdir -p ~/.config/toronto-bids
umask 077
read -rs W && printf 'TB_SLACK_WEBHOOK=%s\n' "$W" > ~/.config/toronto-bids/tb.env
chmod 600 ~/.config/toronto-bids/tb.env
```

If `TB_SLACK_WEBHOOK` is unset, `tb nightly` and `publish-data.sh` run normally without posting to Slack.

### Ariba supplier account (for attachment capture)

The account must not have MFA enabled, since unattended logins cannot answer challenges. Prompting separately works in both bash and zsh:

```shell
umask 077
printf 'ARIBA_USERNAME: '; read -r U
printf 'ARIBA_PASSWORD: '; read -rs P; echo
printf 'ARIBA_USERNAME=%s\nARIBA_PASSWORD=%s\n' "$U" "$P" >> ~/.config/toronto-bids/tb.env
unset U P; chmod 600 ~/.config/toronto-bids/tb.env
```

The first Ariba capture sweeps all open events (1-2 hours). Later runs skip events already bundled on disk.

### GitHub authentication (required for publishing)

`publish-data.sh` requires `gh` to have `repo` scope on `CivicTechTO/toronto-bids-data` (releases) and `workflow` scope on `CivicTechTO/toronto-bids-frontend` (deploy dispatch). Either log in interactively (`gh auth login`, check with `gh auth status`) or append a service token to `tb.env`:

```shell
read -rs T && printf 'GH_TOKEN=%s\n' "$T" >> ~/.config/toronto-bids/tb.env
chmod 600 ~/.config/toronto-bids/tb.env
```

Publishing fails with a non-zero exit code if `gh auth status` fails.

### `ANTHROPIC_API_KEY` (direct Claude document extraction)

Uncached PDFs are sent directly to the Claude Messages API. Use an API key from the Console organization linked to the subscription's included API credits (see [Anthropic's subscriber credit guide](https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers)). Create a dedicated workspace and set a hard spending limit in the Console before unattended use.

Add `ANTHROPIC_API_KEY` to `~/.config/toronto-bids/tb.env` (mode `0600`) via a private editor or secret manager. Placeholders from `scrapers/.env.example` are treated as unset. No key is needed when all eligible documents are already cached. If extraction fails due to a missing key or exhausted budget, downloaded PDFs remain archived.

#### Model and provider settings

- Defaults: `TB_LLM_PROVIDER=anthropic`, `TB_CLAUDE_MODEL=claude-sonnet-5-5`, `TB_LLM_MONTHLY_USD=20`.
- Effort: `TB_CLAUDE_EFFORT` accepts `low`, `medium`, `high`, `xhigh`, or `max` without invalidating the prompt cache. Default is `high` for Sonnet (medium omitted records in live testing) and `medium` for Haiku. Evaluate `claude-haiku-5-5` on representative real documents before making it the default (see [`docs/document-extraction.md`](../docs/document-extraction.md)).
- Override per command with `tb extract --model ...`.
- There is no automatic fallback between paid providers. Legacy OpenRouter is opt-in via `TB_LLM_PROVIDER=openrouter` and `OPENROUTER_API_KEY` (using `openai/gpt-5.6-luna` with Flex).
- Claude requests use schema-constrained JSON output and a 5-minute prompt-cache breakpoint on the shared instruction prefix. Changing the provider does not change `EXTRACTOR_VERSION` or invalidate `extraction_cache`; editing the prompt itself does, so treat prompt edits as a deployment event.

#### Usage and budget accounting

`<TB_DATA_DIR>/llm-usage.sqlite` tracks local spending and is excluded from public exports:

- Before each request, the client reserves a conservative maximum cost estimate (intentionally overestimating input tokens, so requests can stop slightly below the nominal monthly limit).
- Successful responses reconcile the reservation against reported input, output (including thinking), cache-write, and cache-read tokens. Refused, malformed, and truncated outputs still count toward spend.
- HTTP 4xx errors release their reservations. Timeouts, 5xx errors, crashes, and missing usage metadata keep their reservations because billing is uncertain. Retries take a new reservation, and concurrent processes share the same SQLite ledger.
- Unknown models are rejected until their pricing is added to the code (checked 2026-10-23 against the [official price list](https://platform.claude.com/docs/en/about-claude/pricing)).
- Unresolved reservations carry across UTC calendar months; settlements are recorded in the month received. Note that included credits renew on the subscription cycle rather than UTC calendar months, so keep the Console workspace limit as the real spending cap.
- Set `TB_LLM_MONTHLY_USD=0` to block new API calls while allowing cached work to run. Keep `llm-usage.sqlite` across deployments; deleting it resets local spend tracking.

Inspect charges and open reservations offline:

```shell
sqlite3 "$TB_DATA_DIR/llm-usage.sqlite" \
  "SELECT month, status, count(*), sum(charged_microdollars)/1000000.0 AS usd FROM request GROUP BY month, status;"
```

## 4. Install systemd units

### Nightly timer

```shell
mkdir -p ~/.config/systemd/user
cp ~/toronto-bids/deploy/tb-nightly.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
```

Test a manual run before enabling the timer:

```shell
systemctl --user start tb-nightly.service
journalctl --user -u tb-nightly -n 50 --no-pager
```

Then enable the schedule:

```shell
systemctl --user enable --now tb-nightly.timer
systemctl --user list-timers tb-nightly.timer
```

### Hourly batch collector

By default, `tb nightly` queues uncached Claude extraction jobs as asynchronous batches, and an hourly collector imports finished results, derives rows, exports, and publishes without re-scraping sources. Both wrappers coordinate on `<TB_DATA_DIR>/.pipeline.lock` (the collector skips if nightly is active; nightly waits briefly for an active collector). Failed publications leave batch handoffs unacknowledged for retry.

Install and enable the collector:

```shell
cp ~/toronto-bids/deploy/tb-batch-collect.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user start tb-batch-collect.service
journalctl --user -u tb-batch-collect -n 50 --no-pager
systemctl --user enable --now tb-batch-collect.timer
```

The collector runs hourly every day (including weekends) and publishes only when newly completed batch results apply. Set `TB_CLAUDE_MODE=sync` to run nightly extraction synchronously instead. For queue commands, retries, and lost-receipt recovery, see [docs/claude-batches.md](../docs/claude-batches.md).

## Operating commands

| Task | Command |
|---|---|
| View last run | `journalctl --user -u tb-nightly -n 50 --no-pager` |
| Next scheduled run | `systemctl --user list-timers tb-nightly.timer` |
| Trigger run now | `systemctl --user start tb-nightly.service` |
| Row counts and per-source status | `cd ~/toronto-bids/scrapers && TB_DATA_DIR=~/tb-data uv run tb status` |
| Update code | `cd ~/toronto-bids && git pull && cd scrapers && uv sync --locked` |

Updates are manual.

## Schedule: Tuesday through Saturday (#180)

`deploy/tb-nightly.timer` runs at `05:30 America/Toronto`, Tuesday through Saturday (`OnCalendar=Tue..Sat *-*-* 05:30:00 America/Toronto`), plus on the 1st of every month (`OnCalendar=*-*-01 05:30:00 America/Toronto`) so monthly Council enrichment and dated snapshots still run when the 1st falls on a Sunday or Monday. Because a 05:30 run captures the previous day's activity and the City does not publish procurement updates on weekends, ordinary Sunday and Monday mornings are skipped.

- **Tradeoff:** An Ariba posting that opens over a weekend and closes Monday morning has its last capture chance on Saturday morning (~2.5 days early). Already-captured bundles are unaffected.
- **Timestamp behavior:** Over a weekend, `generated_at` in the published export can be up to 3 days old (Saturday through Tuesday morning).
- **Orphaned Ariba timer:** Older deployments may still have a retired `tb-ariba-attachments.timer` unit installed under `~/.config/systemd/user/`. Remove it if present so Ariba capture does not run twice:

```shell
systemctl --user disable --now tb-ariba-attachments.timer
rm ~/.config/systemd/user/tb-ariba-attachments.{timer,service}
systemctl --user daemon-reload
```

## Publishing exports (#146)

`deploy/publish-data.sh` uploads `bids.json`, `bids.json.gz`, and `bids.sqlite` (plus CSV/Parquet/manifest assets) to the rolling `latest` release on `CivicTechTO/toronto-bids-data`:

```
https://github.com/CivicTechTO/toronto-bids-data/releases/download/latest/bids.json
```

Uploads retry up to three times on HTTP 5xx errors (5s / 10s backoff), and the manifest uploads last. On the 1st of each month, the script also creates a dated `snapshot-YYYY-MM-DD` release. After uploading, it dispatches the frontend build (`gh workflow run deploy.yml`, best-effort).

Slack notifications: `tb nightly` posts the archive summary, and `publish-data.sh` posts a one-line publish result (including any rejected frontend deploy dispatch error, #237).

### One-time data repo setup

```shell
sudo apt install -y gh
gh repo create CivicTechTO/toronto-bids-data --public \
  --description "Nightly export of the Toronto procurement archive (data only)."
```

### Dry-run self-test

Runs local artifact validation and gzip, then prints every `gh` command without uploading, notifying Slack, or writing `sync_run` rows:

```shell
cd ~/toronto-bids
TB_PUBLISH_DRY_RUN=1 TB_DATA_DIR=~/tb-data deploy/publish-data.sh
# Test the 1st-of-month snapshot path on any day:
TB_PUBLISH_DRY_RUN=1 TB_PUBLISH_DAY=01 TB_DATA_DIR=~/tb-data deploy/publish-data.sh
```

### Step recording and freshness checks (#176)

`publish-data.sh` sources helpers from `deploy/publish-lib.sh` (tested in `tests/test_publish_lib.py`) to record outcomes in `sync_run`:

- `publish`: overall release upload status.
- `r2_mirror`: recorded only when `CLOUDFLARE_API_TOKEN` is set and an upload is attempted.
- `frontend_deploy_trigger`: `ok` if GitHub accepted the workflow dispatch, or `failed` with the CLI error (does not fail `publish` or monitor the downstream build).
- `github_release_freshness` and `r2_freshness`: after real publishes, `verify_artifact_size` sends a `HEAD` request to each public `bids.sqlite` URL and compares `Content-Length` to the local file size. Read-only and best-effort (never retries or fails `publish`). Override the R2 check URL with `TB_R2_PUBLIC_URL`.

### Recovering a frontend workflow disabled for inactivity (#236)

[GitHub disables scheduled workflows in public repositories after 60 days without repo commits](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows). Releases in `toronto-bids-data` do not count as activity in `toronto-bids-frontend`, so `deploy.yml` can enter `disabled_inactivity` and reject `workflow_dispatch` with HTTP 422.

Check workflow state:

```shell
gh api repos/CivicTechTO/toronto-bids-frontend/actions/workflows/deploy.yml \
  --jq '{state, path, updated_at}'
gh run list -R CivicTechTO/toronto-bids-frontend --workflow deploy.yml --limit 5
```

If disabled, re-enable it and dispatch a build from `main`:

```shell
gh workflow enable deploy.yml -R CivicTechTO/toronto-bids-frontend
gh workflow run deploy.yml -R CivicTechTO/toronto-bids-frontend --ref main
gh run list -R CivicTechTO/toronto-bids-frontend --workflow deploy.yml \
  --event workflow_dispatch --limit 1
gh run watch RUN_ID -R CivicTechTO/toronto-bids-frontend --exit-status
```

Once the run succeeds, check <https://civictechto.github.io/toronto-bids-frontend/> and `/counts.json` to confirm the timestamp and counts match the latest release. No scraper rerun or data re-upload is required.

### Provisioning a fresh host from published archives

A fresh host can start directly from `bids.sqlite` on the `latest` release. To re-derive Bid Award Panel titles and bids offline from the archived agenda HTML (the Panel was abolished on 2025-10-01 and the 891 cached pages are final):

```shell
gh release download council-agendas -R CivicTechTO/toronto-bids-data -D /tmp/agendas
mkdir -p ~/tb-data/council/agendas
unzip -q -o /tmp/agendas/council-agendas.zip -d ~/tb-data/council/agendas
cd ~/toronto-bids/scrapers && TB_DATA_DIR=~/tb-data uv run tb enrich-titles
```

## R2 mirror for Datasette-Lite (#155)

GitHub release assets do not send `Access-Control-Allow-Origin`, so browser-side SQL (Datasette-Lite) cannot load `bids.sqlite` directly from GitHub. `publish-data.sh` mirrors `bids.sqlite` to a CORS-enabled Cloudflare R2 bucket using a Python S3 multipart uploader (avoiding Wrangler's 300 MiB upload limit and requiring no Node at runtime).

- Public URL: `https://pub-99a890c186c743c19ef7bcd00024dca8.r2.dev/bids.sqlite`
- Datasette-Lite: `https://lite.datasette.io/?url=https://pub-99a890c186c743c19ef7bcd00024dca8.r2.dev/bids.sqlite`
- Best-effort: skips when `CLOUDFLARE_API_TOKEN` is unset; a failure warns without failing `publish`.
- Derives S3 credentials in memory from `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` per [Cloudflare's token mapping](https://developers.cloudflare.com/r2/api/tokens/), verifies uploaded size and SHA-256 metadata, and runs while holding the pipeline lock.
- Override bucket name with `TB_R2_BUCKET` (default `toronto-bids-data`).

One-time bucket provisioning (requires Node for `wrangler`; already completed for production):

```shell
# Enable R2 in Cloudflare, create a scoped token (Workers R2 Storage: Edit),
# and add CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID to ~/.config/toronto-bids/tb.env.
wr() { ( set -a; . ~/.config/toronto-bids/tb.env; set +a; npx -y wrangler@latest "$@" ); }
wr r2 bucket create toronto-bids-data
wr r2 bucket dev-url enable toronto-bids-data
# CORS must allow '*' because Datasette-Lite fetches from lite.datasette.io:
printf '{"rules":[{"allowed":{"origins":["*"],"methods":["GET","HEAD"],"headers":["*"]},"maxAgeSeconds":3600}]}' > /tmp/r2-cors.json
wr r2 bucket cors set toronto-bids-data --file /tmp/r2-cors.json
```

## Notes on specific steps

- **Suspended-firm council enrichment (`enrich-council`)**: Runs monthly on the 1st inside `tb nightly` because the suspension list changes rarely. Run manually if needed between monthly runs.
- **Bid Award Panel scraping**: Retired; the Panel was abolished on 2025-10-01 by By-law 766-2025.
- **MERX (`merx` step)**: Runs after Bonfire in `tb nightly` for the five enabled buyers, including public bid/award detail tabs, with no credentials or extra timer. Uses a 5-second minimum interval and persisted `Retry-After` deadlines. Full historical backfills are run manually (see [docs/merx.md](../docs/merx.md)).
