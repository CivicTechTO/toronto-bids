"""Exercise the real publish script with local artifacts and stubbed network commands."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys

import pytest

from tests.helpers_deploy import BASH, DEPLOY
from toronto_bids.export.bundle import COMPLETE_MARKER, write_complete_marker
from toronto_bids.store import db

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("uv") is None,
    reason="publish-data.sh needs bash and uv",
)


def _publish(
    tmp_path,
    error="",
    dry_run=False,
    r2=False,
    r2_error="",
    upload_errors=(),
    day="20",
    write_marker=True,
    tamper_after_marker=None,
):
    data = tmp_path / "data"
    export = data / "export"
    export.mkdir(parents=True)
    generated_at = "2026-10-20T13:00:00Z"
    (export / "bids.json").write_text(json.dumps({"meta": {"generated_at": generated_at}}))
    (export / "schema.json").write_text(json.dumps({"generated_at": generated_at, "tables": {}}))
    (export / "bids-csv.zip").write_bytes(b"csv fixture")
    (export / "ariba_posting.parquet").write_bytes(b"parquet fixture")
    if write_marker:
        write_complete_marker(
            export,
            generated_at,
            ["bids.json", "schema.json", "bids-csv.zip", "ariba_posting.parquet"],
        )
    if tamper_after_marker is not None:
        tamper_after_marker(export)
    conn = db.connect(data / "bids.sqlite")
    db.init_db(conn)
    conn.close()
    uploads_dir = tmp_path / "uploaded_sqlite"
    uploads_dir.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # These are the only network clients the publish script uses with R2 disabled. Both
    # are executables on a private PATH; notification payloads never reach a real webhook.
    gh = bin_dir / "gh"
    gh.write_text(f'''#!{sys.executable}
import json,os,shutil,sys
from pathlib import Path
with open(os.environ['COMMANDS'], 'a') as f:
    f.write(json.dumps(['gh', *sys.argv[1:]])+'\\n')
if sys.argv[1:3] == ['release', 'view'] and len(sys.argv) > 3 and sys.argv[3].startswith('snapshot-'):
    sys.exit(1)
if sys.argv[1:3] == ['release','upload']:
    path=os.path.join(os.environ['TB_DATA_DIR'],'upload_attempts')
    try: n=int(open(path).read())
    except FileNotFoundError: n=0
    open(path,'w').write(str(n+1))
    errors=json.loads(os.environ.get('UPLOAD_ERRORS','[]'))
    if n<len(errors) and errors[n]:
        print(errors[n],file=sys.stderr)
        sys.exit(1)
    for arg in sys.argv[3:]:
        if os.path.basename(arg) == 'bids.sqlite' and os.path.isfile(arg):
            shutil.copy2(arg, os.path.join(os.environ['UPLOADS_DIR'], 'latest-bids.sqlite'))
if sys.argv[1:3] == ['release','create']:
    for arg in sys.argv[3:]:
        if os.path.basename(arg) == 'bids.sqlite' and os.path.isfile(arg):
            shutil.copy2(arg, os.path.join(os.environ['UPLOADS_DIR'], 'snapshot-bids.sqlite'))
if sys.argv[1:3] == ['workflow','run']:
    error=os.environ.get('DISPATCH_ERROR','')
    if error:
        print(error,file=sys.stderr)
        sys.exit(1)
    print('https://github.com/example/frontend/actions/runs/123')
''')
    gh.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text(f'''#!{sys.executable}
import json,os,sys
from pathlib import Path
with open(os.environ['COMMANDS'], 'a') as f:
    f.write(json.dumps(['curl', *sys.argv[1:]])+'\\n')
if '-sIL' in sys.argv:
    uploaded = Path(os.environ['UPLOADS_DIR']) / 'latest-bids.sqlite'
    target = uploaded if uploaded.exists() else Path(os.environ['TB_DATA_DIR']) / 'bids.sqlite'
    print('HTTP/2 200\\r\\nContent-Length: '+str(target.stat().st_size)+'\\r\\n')
else:
    payload=sys.argv[sys.argv.index('--data')+1]
    with open(os.environ['NOTIFICATIONS'],'a') as f:
        f.write(payload+'\\n')
''')
    curl.chmod(0o755)
    uv = bin_dir / 'uv'
    uv.write_text(f'''#!{sys.executable}
import json,os,shutil,sys
if 'toronto_bids.r2_mirror' in sys.argv:
    with open(os.environ['COMMANDS'],'a') as f:
        f.write(json.dumps(['uv',*sys.argv[1:]])+'\\n')
    file_arg = sys.argv[sys.argv.index('--file') + 1]
    if os.path.isfile(file_arg):
        shutil.copy2(file_arg, os.path.join(os.environ['UPLOADS_DIR'], 'r2-bids.sqlite'))
    error=os.environ.get('R2_ERROR','')
    if error:
        print(error,file=sys.stderr)
        sys.exit(1)
    sys.exit(0)
real={shutil.which('uv')!r}
os.execv(real,[real,*sys.argv[1:]])
''')
    uv.chmod(0o755)
    sleeper=bin_dir/'sleep'
    sleeper.write_text('#!/bin/sh\nexit 0\n')
    sleeper.chmod(0o755)
    env = dict(os.environ)
    env.update({
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "TB_DATA_DIR": str(data), "TB_NIGHTLY_UV": str(uv),
        "TB_SLACK_WEBHOOK": "https://hooks.example.invalid/test",
        "CLOUDFLARE_API_TOKEN": "fixture" if r2 else "", "TB_PUBLISH_DAY": day,
        "TB_PUBLISH_DATE": "2026-10-01" if day == "01" else "2026-10-20",
        "TB_PUBLISH_DRY_RUN": "1" if dry_run else "0",
        "COMMANDS": str(tmp_path / "commands.jsonl"),
        "NOTIFICATIONS": str(tmp_path / "notifications.jsonl"),
        "UPLOADS_DIR": str(uploads_dir),
        "DISPATCH_ERROR": error,
        "R2_ERROR": r2_error,
        "UPLOAD_ERRORS": json.dumps(upload_errors),
    })
    result = subprocess.run([BASH, str(DEPLOY / "publish-data.sh")],
                            env=env, text=True, capture_output=True)
    conn = sqlite3.connect(data / "bids.sqlite")
    rows = conn.execute("SELECT source,status,error FROM sync_run ORDER BY id").fetchall()
    conn.close()
    notifications_path = tmp_path / "notifications.jsonl"
    notifications = ([json.loads(line)["text"] for line in notifications_path.read_text().splitlines()]
                     if notifications_path.exists() else [])
    commands_path = tmp_path / "commands.jsonl"
    commands = ([json.loads(line) for line in commands_path.read_text().splitlines()]
                if commands_path.exists() else [])
    return result, rows, notifications, commands


def test_publish_records_accepted_trigger_and_sends_success_notification(tmp_path):
    result, rows, notifications, commands = _publish(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert ("frontend_deploy_trigger", "ok", None) in rows
    assert ("github_release_freshness", "ok", None) in rows
    assert rows[-1] == ("publish", "ok", None)
    assert len(notifications) == 1
    assert notifications[0].startswith("✅ toronto-bids publish — latest release updated")
    assert "⚠" not in notifications[0]
    assert "https://github.com/example/frontend/actions/runs/123" in result.stdout
    assert any(c[:3] == ["gh", "release", "upload"] for c in commands)


@pytest.mark.parametrize("error", [
    "could not create workflow dispatch event: HTTP 422: Cannot trigger a 'workflow_dispatch' on a disabled workflow",
    "HTTP 403: Resource not accessible by integration",
])
def test_rejected_trigger_is_visible_without_failing_data_publication(tmp_path, error):
    result, rows, notifications, commands = _publish(tmp_path, error=error)
    assert result.returncode == 0, result.stdout + result.stderr
    trigger = next(row for row in rows if row[0] == "frontend_deploy_trigger")
    assert trigger[1] == "failed"
    assert error in trigger[2]
    assert ("github_release_freshness", "ok", None) in rows
    assert rows[-1] == ("publish", "ok", None)
    assert len(notifications) == 1
    assert notifications[0].startswith("✅ toronto-bids publish — latest release updated")
    assert "⚠" in notifications[0]
    assert "frontend deploy trigger failed" in notifications[0]
    assert error in notifications[0]
    assert error in result.stderr
    assert any(c[:3] == ["gh", "release", "upload"] for c in commands)


def test_publish_dry_run_does_not_dispatch_record_or_send_notification(tmp_path):
    result, rows, notifications, commands = _publish(tmp_path, dry_run=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DRY-RUN gh workflow run deploy.yml" in result.stdout
    assert rows == []
    assert notifications == []
    assert commands == []


def test_configured_r2_uses_python_uploader_and_records_success(tmp_path):
    result,rows,_,commands=_publish(tmp_path,r2=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert ('r2_mirror','ok',None) in rows
    assert any(c[0]=='uv' and 'toronto_bids.r2_mirror' in c for c in commands)


def test_r2_upload_failure_is_recorded_without_losing_primary_publication(tmp_path):
    result,rows,_,_=_publish(tmp_path,r2=True,r2_error='multipart transfer failed')
    assert result.returncode==0,result.stdout+result.stderr
    mirror=next(r for r in rows if r[0]=='r2_mirror')
    assert mirror[1]=='failed' and 'multipart transfer failed' in mirror[2]
    assert 'multipart transfer failed' in result.stderr
    assert rows[-1]==('publish','ok',None)


def test_configured_r2_dry_run_never_authenticates_or_uploads(tmp_path):
    result,rows,notifications,commands=_publish(tmp_path,r2=True,dry_run=True)
    assert result.returncode==0
    assert 'DRY-RUN python -m toronto_bids.r2_mirror' in result.stdout
    assert rows==[] and notifications==[] and commands==[]


def test_transient_asset_502_retries_complete_upload_before_manifest_and_frontend(tmp_path):
    result,rows,notifications,commands=_publish(tmp_path,upload_errors=['HTTP 502: Server Error',''])
    assert result.returncode==0,result.stdout+result.stderr
    uploads=[c for c in commands if c[:3]==['gh','release','upload']]
    assert len(uploads)==3
    assert uploads[0]==uploads[1], 'retry must re-clobber the same complete artifact set'
    assert not any('manifest.json' in arg for arg in uploads[0])
    assert any('manifest.json' in arg for arg in uploads[2])
    assert commands.index(uploads[2])<next(i for i,c in enumerate(commands) if c[:3]==['gh','workflow','run'])


@pytest.mark.parametrize('error,attempts', [
    ('HTTP 503: Service Unavailable',3),
    ('HTTP 403: Forbidden',1),
    ('HTTP 429: Too Many Requests',1),
    ('network connection failed',1),
])
def test_failed_data_upload_is_bounded_and_does_not_publish_manifest_or_dispatch(tmp_path,error,attempts):
    result,rows,_,commands=_publish(tmp_path,upload_errors=[error]*3)
    assert result.returncode==1
    uploads=[c for c in commands if c[:3]==['gh','release','upload']]
    assert len(uploads)==attempts
    assert not any('manifest.json' in arg for c in uploads for arg in c)
    assert not any(c[:3]==['gh','workflow','run'] for c in commands)
    assert ('publish','failed',"upload to the 'latest' release on CivicTechTO/toronto-bids-data failed") in rows


def test_manifest_upload_failure_does_not_dispatch_frontend(tmp_path):
    result,rows,_,commands=_publish(tmp_path,upload_errors=['','HTTP 403: Forbidden'])
    assert result.returncode==1
    uploads=[c for c in commands if c[:3]==['gh','release','upload']]
    assert len(uploads)==2
    assert any('manifest.json' in arg for arg in uploads[1])
    assert not any(c[:3]==['gh','workflow','run'] for c in commands)
    assert rows[-1][0:2]==('publish','failed')


def test_missing_completion_marker_refuses_publish_even_with_old_artifacts(tmp_path):
    result, rows, _, commands = _publish(tmp_path, write_marker=False)
    assert result.returncode == 1
    assert "incomplete or inconsistent" in result.stderr
    assert not any(c[:3] == ["gh", "release", "upload"] for c in commands)
    assert not any(c[:3] == ["gh", "workflow", "run"] for c in commands)
    assert rows[-1][0:2] == ("publish", "failed")


def test_mixed_fresh_json_and_old_tabular_artifacts_refuses_publish(tmp_path):
    def mix_fresh_json(export):
        (export / "bids.json").write_text(json.dumps({"meta": {"generated_at": "2026-10-21T00:00:00Z"}}))

    result, rows, _, commands = _publish(tmp_path, tamper_after_marker=mix_fresh_json)
    assert result.returncode == 1
    assert not any(c[:3] == ["gh", "release", "upload"] for c in commands)
    assert rows[-1][0:2] == ("publish", "failed")


def test_rolling_r2_and_monthly_snapshot_upload_identical_frozen_sqlite_bytes(tmp_path):
    result, rows, _, commands = _publish(tmp_path, r2=True, day="01")
    assert result.returncode == 0, result.stdout + result.stderr
    # Publish-time operational steps wrote sync_run rows into the working database between uploads.
    assert ("r2_mirror", "ok", None) in rows
    assert ("github_release_freshness", "ok", None) in rows
    assert ("r2_freshness", "ok", None) in rows
    assert rows[-1] == ("publish", "ok", None)

    uploads_dir = tmp_path / "uploaded_sqlite"
    latest_bytes = (uploads_dir / "latest-bids.sqlite").read_bytes()
    r2_bytes = (uploads_dir / "r2-bids.sqlite").read_bytes()
    snapshot_bytes = (uploads_dir / "snapshot-bids.sqlite").read_bytes()
    working_bytes = (tmp_path / "data" / "bids.sqlite").read_bytes()

    assert latest_bytes == r2_bytes == snapshot_bytes
    assert working_bytes != latest_bytes, "working database must have changed from publish-time sync_run writes"
    manifest = json.loads((tmp_path / "data" / "export" / "manifest.json").read_text())
    sqlite_entry = next(a for a in manifest["artifacts"] if a["name"] == "bids.sqlite")
    assert sqlite_entry["bytes"] == len(latest_bytes)
    assert list((tmp_path / "data").glob(".publish-stage.*")) == []


def test_nightly_wrapper_blocks_publish_on_failed_export_and_publishes_after_upstream_failure(tmp_path):
    if shutil.which("flock") is None:
        pytest.skip("needs flock")

    def run_wrapper(case_dir, *, nightly_rc, write_fresh_marker):
        repo = case_dir / "repo"
        deploy = repo / "deploy"
        deploy.mkdir(parents=True)
        (repo / "scrapers").mkdir()
        shutil.copy2(DEPLOY / "tb-nightly-run.sh", deploy)

        data = case_dir / "data"
        export = data / "export"
        export.mkdir(parents=True)
        (export / "bids.json").write_text('{"meta":{"generated_at":"old"}}')
        (export / COMPLETE_MARKER).write_text('{"version":1,"generated_at":"old"}')

        uv = case_dir / "uv"
        uv.write_text(f"""#!{sys.executable}
import os, sys
from pathlib import Path
if os.environ['WRITE_FRESH_MARKER'] == '1':
    (Path(os.environ['TB_DATA_DIR']) / 'export' / {COMPLETE_MARKER!r}).write_text('{{"version":1,"generated_at":"new"}}')
sys.exit(int(os.environ['NIGHTLY_RC']))
""")
        uv.chmod(0o755)

        published = case_dir / "published.txt"
        pub = deploy / "publish-data.sh"
        pub.write_text(f"""#!/usr/bin/env bash
if [ ! -f "$TB_DATA_DIR/export/{COMPLETE_MARKER}" ]; then
  echo "missing marker" >&2
  exit 1
fi
cat "$TB_DATA_DIR/export/{COMPLETE_MARKER}" > "$PUBLISHED_OUT"
exit 0
""")
        pub.chmod(0o755)

        env = dict(
            os.environ,
            TB_NIGHTLY_UV=str(uv),
            TB_DATA_DIR=str(data),
            NIGHTLY_RC=str(nightly_rc),
            WRITE_FRESH_MARKER="1" if write_fresh_marker else "0",
            PUBLISHED_OUT=str(published),
        )
        res = subprocess.run([BASH, str(deploy / "tb-nightly-run.sh")], env=env, capture_output=True, text=True)
        return res, published

    # Case 1: failed export leaves old artifacts on disk without fresh marker -> publish refused.
    res1, pub1 = run_wrapper(tmp_path / "failed_export", nightly_rc=1, write_fresh_marker=False)
    assert res1.returncode == 1
    assert not pub1.exists()
    assert "missing marker" in res1.stderr

    # Case 2: upstream capture failed (nightly_rc=1) but export completed -> publish succeeds, unit exits 1.
    res2, pub2 = run_wrapper(tmp_path / "upstream_fail", nightly_rc=1, write_fresh_marker=True)
    assert res2.returncode == 1
    assert pub2.exists() and '"new"' in pub2.read_text()


def test_publish_snapshot_date_uses_toronto_timezone_when_host_is_utc(tmp_path):
    script = (DEPLOY / "publish-data.sh").read_text()
    assert 'DAY="${TB_PUBLISH_DAY:-$(TZ=America/Toronto date +%d)}"' in script
    assert 'SNAPSHOT_DATE="${TB_PUBLISH_DATE:-$(TZ=America/Toronto date +%F)}"' in script
    # Confirm bash with TZ=UTC still computes Toronto date when evaluating that expression.
    res = subprocess.run(
        [BASH, "-c", 'TZ=UTC; test "$(TZ=America/Toronto date -d "2026-12-01 02:00:00 UTC" +%F)" = "2026-11-30"'],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
