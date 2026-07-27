"""`deploy/resolve-node.sh` — putting a wrangler-capable Node on PATH (#173).

The R2 mirror of bids.sqlite failed on every nightly run from 2026-07-19 to 2026-07-27 and
nobody saw it. Root cause: publish-data.sh gated its nvm fallback on `command -v npx`, and
Ubuntu's `nodejs` package puts a **Node 20** `/usr/bin/npx` on systemd's minimal PATH. The
guard was satisfied, the fallback never fired, and wrangler (>= 4 needs Node >= 22) exited 1
in under a second — straight into `/dev/null`.

So the rule these tests pin is: **probe the version, never mere presence.**

Bash-subprocess tests are a new shape for this suite (everything else is pure Python), and
deliberately so — the deploy scripts had zero coverage, which is how this reached production.
"""
import shutil
import subprocess

import pytest

from tests.helpers_deploy import RESOLVE_NODE, fake_node, run_resolver

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="resolve-node.sh needs bash"
)


def test_the_helper_exists():
    assert RESOLVE_NODE.exists(), f"missing {RESOLVE_NODE}"


def test_a_too_old_node_on_path_is_rejected_in_favour_of_nvm(tmp_path):
    """The exact production failure: Node 20 on PATH, Node 22 in nvm."""
    path_dir = fake_node(tmp_path / "usr-bin", "v20.20.2")
    home = tmp_path / "home"
    nvm = fake_node(home / ".nvm/versions/node/v25.4.0/bin", "v25.4.0")

    res = run_resolver(tmp_path, path_dirs=[path_dir], home=home)

    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == str(nvm / "node"), (
        f"resolved {res.stdout.strip()!r}, expected the nvm node at {nvm / 'node'}"
    )


def test_a_new_enough_node_on_path_is_kept(tmp_path):
    """No nvm rummaging when PATH already satisfies wrangler."""
    path_dir = fake_node(tmp_path / "usr-bin", "v22.1.0")
    home = tmp_path / "home"
    home.mkdir()

    res = run_resolver(tmp_path, path_dirs=[path_dir], home=home)

    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == str(path_dir / "node")


def test_it_picks_the_newest_SUITABLE_nvm_node_not_merely_the_newest(tmp_path):
    """`sort -V | tail -1` blindly takes the newest; if that one is too old we must keep looking."""
    path_dir = fake_node(tmp_path / "usr-bin", "v20.20.2")
    home = tmp_path / "home"
    good = fake_node(home / ".nvm/versions/node/v22.9.0/bin", "v22.9.0")
    fake_node(home / ".nvm/versions/node/v21.0.0/bin", "v21.0.0")

    res = run_resolver(tmp_path, path_dirs=[path_dir], home=home)

    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == str(good / "node")


def test_no_suitable_node_anywhere_fails_loudly(tmp_path):
    """The mirror must report 'no usable node', never proceed into a doomed wrangler call."""
    path_dir = fake_node(tmp_path / "usr-bin", "v20.20.2")
    home = tmp_path / "home"
    fake_node(home / ".nvm/versions/node/v18.0.0/bin", "v18.0.0")

    res = run_resolver(tmp_path, path_dirs=[path_dir], home=home)

    assert res.returncode != 0


def test_no_node_at_all_fails_rather_than_crashing(tmp_path):
    """An empty PATH must return non-zero, not blow up on an unbound variable."""
    home = tmp_path / "home"
    home.mkdir()

    res = run_resolver(tmp_path, path_dirs=[], home=home)

    assert res.returncode != 0


def test_an_unrunnable_node_is_treated_as_absent(tmp_path):
    """A `node` that exists but cannot execute must not be mistaken for a usable one."""
    broken = tmp_path / "usr-bin"
    broken.mkdir(parents=True)
    (broken / "node").write_text("#!/bin/sh\nexit 127\n")
    (broken / "node").chmod(0o755)
    home = tmp_path / "home"
    good = fake_node(home / ".nvm/versions/node/v22.9.0/bin", "v22.9.0")

    res = run_resolver(tmp_path, path_dirs=[broken], home=home)

    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == str(good / "node")


def test_the_minimum_major_is_overridable(tmp_path):
    """TB_NODE_MIN_MAJOR tracks wrangler's requirement without editing the script."""
    path_dir = fake_node(tmp_path / "usr-bin", "v20.20.2")
    home = tmp_path / "home"
    home.mkdir()

    res = run_resolver(tmp_path, path_dirs=[path_dir], home=home,
                       env={"TB_NODE_MIN_MAJOR": "20"})

    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == str(path_dir / "node")


def test_publish_data_no_longer_gates_the_fallback_on_mere_presence():
    """Regression guard on the actual defect: `! command -v npx` must not gate the R2 step."""
    publish = RESOLVE_NODE.parent / "publish-data.sh"
    body = publish.read_text()
    assert "! command -v npx" not in body, (
        "publish-data.sh is gating on npx PRESENCE again — that is the #173 bug: "
        "Ubuntu's Node 20 npx satisfies it and wrangler then dies."
    )
    assert "resolve-node.sh" in body, "publish-data.sh should source the shared resolver"


def test_wrangler_stderr_is_not_discarded():
    """The reason #173 ran unseen for 8 nights: the error went to /dev/null.

    Scoped to the R2 block alone — later sections discard stderr legitimately (e.g. the
    `gh release view` existence probe for the monthly snapshot).
    """
    publish = (RESOLVE_NODE.parent / "publish-data.sh").read_text()
    start = publish.index("R2_BUCKET=")
    r2 = publish[start:publish.index("\n# 6.", start)]
    assert "wrangler" in r2, "slice missed the R2 block — retarget this test"
    assert ">/dev/null 2>&1" not in r2, (
        "the R2 upload is discarding wrangler's stderr again (#173)"
    )
