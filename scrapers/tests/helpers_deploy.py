"""Helpers for testing bash scripts under deploy/ from pytest."""
import os
import pathlib
import shutil
import subprocess

DEPLOY = pathlib.Path(__file__).resolve().parents[2] / "deploy"
RESOLVE_NODE = DEPLOY / "resolve-node.sh"
PUBLISH_LIB = DEPLOY / "publish-lib.sh"
SCRAPERS_DIR = pathlib.Path(__file__).resolve().parents[1]

# Resolve bash before tests override PATH with stub directories.
BASH = shutil.which("bash") or "/bin/bash"


def fake_node(bin_dir: pathlib.Path, version: str) -> pathlib.Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    node = bin_dir / "node"
    node.write_text(f'#!/bin/sh\n[ "$1" = --version ] && echo "{version}"\n')
    node.chmod(0o755)
    return bin_dir


def run_resolver(tmp_path, path_dirs, home, env=None) -> subprocess.CompletedProcess:
    script = (
        f'. "{RESOLVE_NODE}"\n'
        "tb_resolve_node || exit 1\n"
        'command -v node\n'
    )
    full_env = {
        "PATH": os.pathsep.join(str(p) for p in path_dirs),
        "HOME": str(home),
    }
    full_env.update(env or {})
    return subprocess.run(
        [BASH, "-c", script],
        capture_output=True, text=True, env=full_env, cwd=tmp_path,
    )


def run_publish_lib(tmp_path, script_body: str, env=None) -> subprocess.CompletedProcess:
    script = f'. "{PUBLISH_LIB}"\n{script_body}\n'
    full_env = dict(os.environ)
    full_env.update(env or {})
    return subprocess.run(
        [BASH, "-c", script],
        capture_output=True, text=True, env=full_env, cwd=tmp_path,
    )
