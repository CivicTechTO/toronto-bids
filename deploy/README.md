# Deploying the nightly job

Design: [`docs/superpowers/specs/2026-07-17-deployment-design.md`](../docs/superpowers/specs/2026-07-17-deployment-design.md)

User-level systemd. `tb nightly` is a single unified run: sync, award summaries, MERX, Ariba
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
uv sync --locked                        # playwright + pyvirtualdisplay + python-dotenv are base deps now
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

## 3. Credentials — the Slack webhook, the publish token and the extraction key

The repo is public. All of these are credentials and live only here, mode `0600`:

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

### `ANTHROPIC_API_KEY` — direct Claude document extraction

Uncached document text goes directly to the Claude Messages API. Use a key in the
Console organization linked to the subscription's included API credits. Those credits
apply to direct Claude API usage; OpenRouter billing is separate. Create a dedicated
scraper workspace and set its spending limit in the Console before unattended use.
See [Anthropic's subscriber credit guide](https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers).

Set `ANTHROPIC_API_KEY` in `~/.config/toronto-bids/tb.env`, mode `0600`, using a private
editor or secret manager. Do not paste the key into a ticket, chat or shell history.
The placeholders in `scrapers/.env.example` read as unset. No key is needed when all
eligible documents already have cached extraction results. Downloads remain archived
if extraction fails for a missing key or exhausted budget.

The default is `TB_LLM_PROVIDER=anthropic`, `TB_CLAUDE_MODEL=claude-sonnet-5-5` and
`TB_LLM_MONTHLY_USD=20`. Sonnet uses high effort after live testing found an
omission at medium effort; the supported
`claude-haiku-5-5` option must pass a representative real-document evaluation before
becoming the production default. `tb extract --model ...` overrides the model for
both single-document and corpus commands. `TB_CLAUDE_EFFORT` optionally selects
`low`, `medium`, `high`, `xhigh` or `max` without a prompt edit; unset uses Sonnet
`high` or Haiku `medium`. Higher effort is bounded by the same output-token and
monthly budget controls. The measured tradeoffs, including batch processing, are
in `docs/claude-cost-options-2026-10-23.md`. There is no automatic paid-provider
fallback. Legacy OpenRouter remains available only with `TB_LLM_PROVIDER=openrouter`
and `OPENROUTER_API_KEY`, using `openai/gpt-5.6-luna` with Flex; the rejected free
model has been removed.

Claude requests schema-constrained JSON rather than relying on the prompt alone to
avoid prose or malformed responses. The shared extraction prompt and
`EXTRACTOR_VERSION` are unchanged. The existing
per-document cache stays usable; provider migration does not trigger a corpus
re-extraction. Claude also receives a five-minute cache breakpoint on the shared
instruction prefix, leaving each varying document outside that cache breakpoint.

#### Usage and budget accounting

`<TB_DATA_DIR>/llm-usage.sqlite` is private operational state, excluded from public
procurement exports. Each Claude attempt reserves a conservative maximum allowance
before network access. Successful responses replace that allowance with estimated
cost from reported input, output (including thinking), cache-write and cache-read
tokens. Refused, malformed and truncated outputs are billed and counted too. Definite
HTTP 4xx rejections release their allowance. Timeouts, 5xx responses, process crashes
and unusable usage metadata keep the reservation because billing is uncertain.
Retries require a fresh allowance, and concurrent processes share the same ledger.

The guard uses reviewed per-model prices; unknown models are refused until their
prices are added. Prices were checked on 23 October 2026 against the
[official price list](https://platform.claude.com/docs/en/about-claude/pricing).
Reservations deliberately overestimate input tokens and can stop a request before
the nominal remaining balance is fully consumed. Use the Console workspace limit
as the account billing authority: the local estimate does not control other keys,
applications, provider pricing changes or legacy OpenRouter calls.

Outstanding reservations carry into subsequent calendar months until reconciled;
a batch crossing midnight cannot bypass the new month's guard. Settlement is
booked conservatively in the month received. Inspect charges and unresolved reservations without API access:

```shell
sqlite3 "$TB_DATA_DIR/llm-usage.sqlite" \
  "SELECT month, status, count(*), sum(charged_microdollars)/1000000.0 AS usd FROM request GROUP BY month, status;"
```

`TB_LLM_MONTHLY_USD=0` blocks new Claude requests while cached work continues.
Changing the limit does not erase earlier usage. Retain the ledger across deployments;
deleting it resets the local accounting. Its months are UTC calendar months, whereas
included credits renew on the subscription billing cycle. Investigate uncertain
reservations against Console billing before adjusting operational state.

A prompt edit still changes the cache version and can re-extract the entire corpus;
treat it as a deployment event.

## 4. Units

```shell
mkdir -p ~/.config/systemd/user
cp ~/toronto-bids/deploy/tb-nightly.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
```

### Asynchronous extraction collector

The nightly queues uncached Sonnet work and the hourly collector imports completed
results, derives supported facts from held sources, exports and publishes. Both
wrappers use `<TB_DATA_DIR>/.pipeline.lock`: the collector skips an active nightly,
and the nightly waits for a short collector. No new source scraping occurs in the
collector. Failed publication keeps its document handoff unacknowledged for retry.

Install the collector alongside the nightly after deploying the code:

```shell
cp ~/toronto-bids/deploy/tb-batch-collect.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user start tb-batch-collect.service
journalctl --user -u tb-batch-collect -n 50 --no-pager
systemctl --user enable --now tb-batch-collect.timer
```

The collector runs hourly on all days, including weekends. It publishes only when
completed results need applying. Pending work is explicit in the nightly summary;
its presence alone does not fail the unit. API, derivation or publishing failures
still produce nonzero status. For queue operations, failed-job retry and lost
receipt recovery, read [Claude batches](../docs/claude-batches.md).
`TB_CLAUDE_MODE=sync` restores immediate nightly extraction. Standalone commands
remain immediate unless `tb extract --batch --corpus ...` is selected.

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

## Schedule: Tuesday through Saturday, not daily (#180)

The City does not transact on weekends, and a 05:30 run reports on the **previous** day — so
Saturday's run (covering Friday, a full business day) is worth keeping, but Sunday's (covering
Saturday) and Monday's (covering Sunday) are not. Measured over an 11-day window: zero new rows
in any table on either day, and the City's own Ariba `close_date` values never land on a
weekend at all (n=48 dated postings). `deploy/tb-nightly.timer` reflects this:
`OnCalendar=Tue..Sat *-*-* 05:30:00 America/Toronto`.

**Accepted tradeoff:** a solicitation whose window opens only over the weekend and closes Monday
at noon gets its last capture chance at Saturday's run — about 2.5 days out, versus same-day
Monday under the old daily schedule. The Ariba capture step is idempotent and resumable (a
bundle already on disk is never refetched), so this only affects what a single run might miss
entirely, not anything already captured on an earlier day.

**A side effect worth knowing about:** skipping two nights means the published artifacts'
`generated_at` can be up to 3 days old over a weekend (Saturday's run through Tuesday's). That is
accurate rather than stale data going unnoticed — nothing changed at the source — but if the
frontend ever surfaces a bare "last updated" timestamp, it is worth a line explaining the
weekday cadence rather than reading as an outage.

**If you find a `tb-ariba-attachments.timer`/`.service` under `~/.config/systemd/user/` on a
live box, it should not be there.** Ariba attachment capture was unified into `tb nightly`'s
in-line steps on 2026-07-19 (#135/#146) and the standalone timer was removed from git the same
day — but on at least one deployment the installed unit files were never actually disabled, so
Ariba capture ran twice a day (05:30 inline, plus the orphaned noon timer) for over a week before
#180 found it via `systemctl --user list-timers`. Finish the retirement:

```shell
systemctl --user disable --now tb-ariba-attachments.timer
rm ~/.config/systemd/user/tb-ariba-attachments.{timer,service}
systemctl --user daemon-reload
```

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

The publish message also warns when the frontend deploy **trigger** is rejected (#237),
including the GitHub CLI error. The attempt is recorded as `frontend_deploy_trigger` in
`sync_run`, with `ok` meaning dispatch accepted and `failed` carrying the error. This does not
monitor the later build or deployment. A rejected trigger leaves `publish` successful when
the data release succeeded, and does not skip the artifact freshness checks. Dry-runs send
no notification and write no step-history rows.

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

### Recovering a frontend workflow disabled for inactivity (#236)

Data publication and frontend deployment are separate outcomes. A successful data release can
coexist with a stale website when GitHub rejects the frontend dispatch. On 2026-10-20, the
frontend's `deploy.yml` was `disabled_inactivity`, and dispatch returned HTTP 422: "Cannot
trigger a 'workflow_dispatch' on a disabled workflow." Its last successful deployment was
2026-09-19, and its last commit was 2026-07-21.

[GitHub automatically disables scheduled workflows in public repositories after 60 days
without repository activity](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows).
The frontend workflow has both `schedule` and `workflow_dispatch` triggers; the disabled state
also prevents the nightly's explicit dispatch. Fresh releases in the **data repository** did
not keep the **frontend repository's** workflow active in this incident. Re-enabling restores
operation but does not exempt the workflow from GitHub's inactivity policy.

Inspect the workflow state before changing anything:

```shell
gh api repos/CivicTechTO/toronto-bids-frontend/actions/workflows/deploy.yml \
  --jq '{state, path, updated_at}'
gh run list -R CivicTechTO/toronto-bids-frontend --workflow deploy.yml --limit 5
```

For `disabled_inactivity`, re-enable the existing workflow and dispatch it against `main`:

```shell
gh workflow enable deploy.yml -R CivicTechTO/toronto-bids-frontend
gh workflow run deploy.yml -R CivicTechTO/toronto-bids-frontend --ref main
gh run list -R CivicTechTO/toronto-bids-frontend --workflow deploy.yml \
  --event workflow_dispatch --limit 1
# Use the run ID returned above:
gh run watch RUN_ID -R CivicTechTO/toronto-bids-frontend --exit-status
```

Verify `state: active`, acceptance of the dispatch, and completion of the deploy job. Its
existing steps fetch the latest `bids.json`, compare against the deployed `counts.json` to
reject archive shrinkage, build Astro/Pagefind, verify internal links, and deploy to Pages.
A dispatch returning successfully is not proof that the deployment completed. If the run
fails, inspect its failed step rather than disabling the shrink or link checks.

Recovery on 2026-10-20 was verified with
[deployment run 39427672769](https://github.com/CivicTechTO/toronto-bids-frontend/actions/runs/39427672769).
The workflow returned to `active`; its dispatch, data fetch, shrink guard, build, internal-link
checks, and Pages deployment succeeded. The public site's data timestamp matched the recovered
export (`2026-10-20T13:00:07.510704+00:00`), and all seven published entity counts matched it,
including 7,565 solicitations, 15,554 award lines, and 8,577 suppliers.

Finally check the public site at
<https://civictechto.github.io/toronto-bids-frontend/>: its exported-data timestamp should match
the intended release, and `/counts.json` should reflect the recovered archive. Allow for
Pages/CDN propagation. No scraper rerun or data re-upload is needed when only the frontend
workflow was disabled. Publish-notification handling for rejected dispatches is tracked
separately in #237.

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

**This step needs Node >= 22, and the distro's Node is not it (#173).** Ubuntu's `nodejs`
package installs `/usr/bin/node` + `/usr/bin/npx` at **v20**, and `/usr/bin` *is* on systemd's
minimal PATH — so a `command -v npx` check passes on the server while `wrangler` (>= 4 requires
Node >= 22) exits 1 immediately. That combination silently broke this mirror on every nightly
run from 2026-07-19 to 2026-07-27: the commands above work when you paste them into a login
shell (nvm's Node is first on *your* PATH) and fail under systemd. `deploy/resolve-node.sh`
now resolves a Node by **version, never by presence**, preferring the newest suitable nvm
install; `TB_NODE_MIN_MAJOR` tracks wrangler's floor. Keep an nvm Node >= 22 installed on the
box — there is no other source of one.

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

**A "best-effort" step that only warns is invisible to `tb status` unless it says so itself
(#176).** The 8-night R2 outage above was findable only by grepping the journal: `publish-data.sh`
kept exiting 0 (the GitHub release is the deliverable) and `tb status` showed 14/14 ok, because
neither `publish` nor the R2 mirror wrote anything to `sync_run`. The script now calls
`tb record-step NAME {ok,failed} [--error TEXT]` — `publish` overall (from `fail()` on every
early-exit guard, and once at the end on success) and `r2_mirror` as its own row, recorded only
on a real attempt (success, a wrangler failure, or no suitable Node) — the deliberate
not-configured skip (no `CLOUDFLARE_API_TOKEN`) gets no row, same as the nightly's own
`council: skip (not the 1st)`. `TB_PUBLISH_DRY_RUN=1` skips recording entirely.
`record_step`/`verify_artifact_size` live in `deploy/publish-lib.sh`, sourced by
`publish-data.sh` — split out purely so `tests/test_publish_lib.py` can drive them directly.

**Recording an exit code is not the same as verifying the artifact (#176, the deferred half of
the R2 incident above).** The wrangler command that failed for 8 nights was at least failing
*loudly* once its stderr was captured — but a step recording "I exited 0" is still one step
removed from "the thing I was supposed to publish is actually there and the right size." After
every real publish (never in dry-run), `verify_artifact_size` sends a HEAD request to
`bids.sqlite`'s two public URLs and compares `Content-Length` to the local file, recording
`github_release_freshness` and (only when R2 is configured for this deployment) `r2_freshness`
as their own `sync_run` rows — distinct from `publish`/`r2_mirror`, and running regardless of
which upload branch fired tonight, so it also catches a short write that an upload command
reported as a success. `TB_R2_PUBLIC_URL` overrides the R2 dev URL this check reads (default is
the one two sections up). Read-only, best-effort: never retries or re-uploads, and a mismatch
here does not fail the overall `publish` step.

## What does NOT run here

`enrich-council` needs a **headed** Chromium (TMMIS is Akamai-gated and blocks headless), but
Playwright is installed as part of the base setup now (§1, "Browser prerequisites") — it runs
monthly inside the nightly (gated to the 1st), so there is nothing separate to schedule for it.

- The Bid Award Panel title-recovery scrape **will never find another agenda** — the Panel was
  abolished on 2025-10-01 by By-law 766-2025, and the 891 cached pages are the final corpus.
- `enrich-council` covers 3 suspended firms and changes rarely; it runs monthly inside the
  nightly (gated to the 1st) rather than every run.

Run either by hand if ever needed outside the nightly's own schedule.

### MERX capture

The nightly `merx` step captures public listings and advertised public bid/award
tabs for the five enabled buyer profiles after Bonfire. It requires no credentials
or new timer. Requests identify the archive and serialize with a five-second
minimum interval and persistent Retry-After deadlines. The summary reports listing
counts and actual bid/award growth; a buyer failure or detail gap fails the step
while the remaining buyers, later steps and export continue. Full historical
backfills remain a separate operation; see [MERX operation](../docs/merx.md).
