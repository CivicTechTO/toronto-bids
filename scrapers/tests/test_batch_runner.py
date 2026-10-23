"""Run the real collector shell wrapper with local command stubs."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(not shutil.which('flock'), reason='Linux deployment requires flock')
DEPLOY = Path(__file__).resolve().parents[2] / 'deploy'


def run(tmp_path, *, handoff=False, collect_rc=0, publish_rc=0, locked=False):
    target = tmp_path / 'repo/deploy'
    target.mkdir(parents=True)
    (target.parent / 'scrapers').mkdir()
    shutil.copy2(DEPLOY / 'tb-batch-collect.sh', target)
    log = tmp_path / 'log.jsonl'
    uv = tmp_path / 'uv'
    uv.write_text(f'''#!{sys.executable}
import json,os,sys
from pathlib import Path
a=sys.argv[1:]; action=a[3]
with open(os.environ['LOG'],'a') as f:f.write(json.dumps(action)+'\\n')
if action=='refresh':
 if os.environ['TEST_WRITE_HANDOFF']=='1':Path(a[a.index('--manifest')+1]).write_text('{{"documents":["one"]}}')
 sys.exit(int(os.environ['COLLECT_RC']))
''')
    uv.chmod(0o755)
    publisher = target / 'publish-data.sh'
    publisher.write_text('#!/usr/bin/env bash\nprintf \'"publish"\\n\' >> "$LOG"\nexit "$PUBLISH_RC"\n')
    publisher.chmod(0o755)
    data = tmp_path / 'data'; data.mkdir()
    env = dict(os.environ, LOG=str(log), TEST_WRITE_HANDOFF=str(int(handoff)), COLLECT_RC=str(collect_rc),
               PUBLISH_RC=str(publish_rc), TB_NIGHTLY_UV=str(uv), TB_DATA_DIR=str(data))
    handle = None
    if locked:
        import fcntl
        handle = (data / '.pipeline.lock').open('w')
        fcntl.flock(handle, fcntl.LOCK_EX)
    try:
        result = subprocess.run(['bash', str(target/'tb-batch-collect.sh')], env=env, capture_output=True, text=True)
    finally:
        if handle:handle.close()
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return result, calls


def test_no_results_means_no_publication(tmp_path):
    result,calls=run(tmp_path)
    assert result.returncode==0 and calls==['refresh']


def test_acknowledgement_follows_successful_publication(tmp_path):
    result,calls=run(tmp_path,handoff=True)
    assert result.returncode==0 and calls==['refresh','publish','acknowledge']


def test_failed_publish_never_acknowledges(tmp_path):
    result,calls=run(tmp_path,handoff=True,publish_rc=1)
    assert result.returncode==1 and calls==['refresh','publish']


def test_supported_partial_results_publish_without_hiding_failure(tmp_path):
    result,calls=run(tmp_path,handoff=True,collect_rc=1)
    assert result.returncode==1 and calls==['refresh','publish','acknowledge']


def test_collector_skips_while_nightly_owns_lock(tmp_path):
    result,calls=run(tmp_path,handoff=True,locked=True)
    assert result.returncode==0 and calls==[]
