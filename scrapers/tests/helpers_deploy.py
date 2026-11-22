"""Helpers for testing bash scripts under deploy/ from pytest."""
import os
import pathlib
import shutil
import subprocess

DEPLOY = pathlib.Path(__file__).resolve().parents[2] / "deploy"
PUBLISH_LIB = DEPLOY / "publish-lib.sh"
SCRAPERS_DIR = pathlib.Path(__file__).resolve().parents[1]

# Resolve bash before tests override PATH with stub directories.
BASH = shutil.which("bash") or "/bin/bash"


def run_publish_lib(tmp_path, script_body: str, env=None) -> subprocess.CompletedProcess:
    script = f'. "{PUBLISH_LIB}"\n{script_body}\n'
    full_env = dict(os.environ)
    full_env.update(env or {})
    return subprocess.run(
        [BASH, "-c", script],
        capture_output=True, text=True, env=full_env, cwd=tmp_path,
    )
