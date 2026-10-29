"""Exercise the real publish script with local artifacts and stubbed network commands."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys

import pytest

from tests.helpers_deploy import BASH, DEPLOY
from toronto_bids.store import db

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("uv") is None,
    reason="publish-data.sh needs bash and uv",
)


def _publish(tmp_path, error="", dry_run=False, r2=False, r2_error=""):
    data = tmp_path / "data"
    export = data / "export"
    export.mkdir(parents=True)
    (export / "bids.json").write_text(json.dumps({"meta": {"generated_at": "2026-10-20T13:00:00Z"}}))
    (export / "schema.json").write_text("{}")
    (export / "bids-csv.zip").write_bytes(b"csv fixture")
    (export / "ariba_posting.parquet").write_bytes(b"parquet fixture")
    conn = db.connect(data / "bids.sqlite")
    db.init_db(conn)
    conn.close()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # These are the only network clients the publish script uses with R2 disabled. Both
    # are executables on a private PATH; notification payloads never reach a real webhook.
    gh = bin_dir / "gh"
    gh.write_text(f'''#!{sys.executable}
import json,os,sys
with open(os.environ['COMMANDS'], 'a') as f:
    f.write(json.dumps(['gh', *sys.argv[1:]])+'\\n')
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
    print('HTTP/2 200\\r\\nContent-Length: '+str((Path(os.environ['TB_DATA_DIR'])/'bids.sqlite').stat().st_size)+'\\r\\n')
else:
    payload=sys.argv[sys.argv.index('--data')+1]
    with open(os.environ['NOTIFICATIONS'],'a') as f:
        f.write(payload+'\\n')
''')
    curl.chmod(0o755)
    uv = bin_dir / 'uv'
    uv.write_text(f'''#!{sys.executable}
import json,os,sys
if 'toronto_bids.r2_mirror' in sys.argv:
    with open(os.environ['COMMANDS'],'a') as f:
        f.write(json.dumps(['uv',*sys.argv[1:]])+'\\n')
    error=os.environ.get('R2_ERROR','')
    if error:
        print(error,file=sys.stderr)
        sys.exit(1)
    sys.exit(0)
real={shutil.which('uv')!r}
os.execv(real,[real,*sys.argv[1:]])
''')
    uv.chmod(0o755)
    env = dict(os.environ)
    env.update({
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "TB_DATA_DIR": str(data), "TB_NIGHTLY_UV": str(uv),
        "TB_SLACK_WEBHOOK": "https://hooks.example.invalid/test",
        "CLOUDFLARE_API_TOKEN": "fixture" if r2 else "", "TB_PUBLISH_DAY": "20",
        "TB_PUBLISH_DRY_RUN": "1" if dry_run else "0",
        "COMMANDS": str(tmp_path / "commands.jsonl"),
        "NOTIFICATIONS": str(tmp_path / "notifications.jsonl"),
        "DISPATCH_ERROR": error,
        "R2_ERROR": r2_error,
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
