"""Helpers for exercising the bash under `deploy/` from pytest (#173).

The deploy scripts are the one part of the pipeline with no test coverage, which is how the
R2 mirror shipped broken and stayed broken for eight nights. These helpers keep the bash
testable without a bash test framework: build a fake PATH, run one function, read the answer.
"""
import os
import pathlib
import shutil
import subprocess

DEPLOY = pathlib.Path(__file__).resolve().parents[2] / "deploy"
RESOLVE_NODE = DEPLOY / "resolve-node.sh"
PUBLISH_LIB = DEPLOY / "publish-lib.sh"
SCRAPERS_DIR = pathlib.Path(__file__).resolve().parents[1]

# The tests hand bash a PATH containing ONLY stub dirs, so nothing may be looked up on it —
# not bash, not the stubs' interpreter. Absolute paths throughout; `#!/bin/sh` rather than
# `/usr/bin/env bash`, since env would resolve `bash` against the doctored PATH and fail.
BASH = shutil.which("bash") or "/bin/bash"


def fake_node(bin_dir: pathlib.Path, version: str) -> pathlib.Path:
    """A stub `node` that reports `version`, so tests need no real Node installs."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    node = bin_dir / "node"
    node.write_text(f'#!/bin/sh\n[ "$1" = --version ] && echo "{version}"\n')
    node.chmod(0o755)
    return bin_dir


def run_resolver(tmp_path, path_dirs, home, env=None) -> subprocess.CompletedProcess:
    """Source resolve-node.sh under a controlled PATH/HOME and print the node it selects.

    Exits non-zero when the resolver finds nothing suitable, so `returncode` is the contract.
    """
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
    """Source publish-lib.sh and run `script_body` after it (#176).

    Unlike `run_resolver`, this inherits the real PATH: `record_step` shells out to the real
    `tb` CLI via `uv`, and `verify_artifact_size` shells out to real `curl` — these are the
    exact functions publish-data.sh calls in production, just without the whole publish flow
    (a real GitHub release, wrangler, Slack) around them. `env` sets/overrides DRY_RUN, UV,
    SCRAPERS, TB_DATA_DIR — the variables publish-lib.sh's functions read as globals.
    """
    script = f'. "{PUBLISH_LIB}"\n{script_body}\n'
    full_env = dict(os.environ)
    full_env.update(env or {})
    return subprocess.run(
        [BASH, "-c", script],
        capture_output=True, text=True, env=full_env, cwd=tmp_path,
    )
