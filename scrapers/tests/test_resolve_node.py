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
    path_dir = fake_node(tmp_path / "usr-bin", "v20.20.2")
    home = tmp_path / "home"
    nvm = fake_node(home / ".nvm/versions/node/v25.4.0/bin", "v25.4.0")

    res = run_resolver(tmp_path, path_dirs=[path_dir], home=home)

    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == str(nvm / "node"), (
        f"resolved {res.stdout.strip()!r}, expected the nvm node at {nvm / 'node'}"
    )


def test_a_new_enough_node_on_path_is_kept(tmp_path):
    path_dir = fake_node(tmp_path / "usr-bin", "v22.1.0")
    home = tmp_path / "home"
    home.mkdir()

    res = run_resolver(tmp_path, path_dirs=[path_dir], home=home)

    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == str(path_dir / "node")


def test_it_picks_the_newest_SUITABLE_nvm_node_not_merely_the_newest(tmp_path):
    path_dir = fake_node(tmp_path / "usr-bin", "v20.20.2")
    home = tmp_path / "home"
    good = fake_node(home / ".nvm/versions/node/v22.9.0/bin", "v22.9.0")
    fake_node(home / ".nvm/versions/node/v21.0.0/bin", "v21.0.0")

    res = run_resolver(tmp_path, path_dirs=[path_dir], home=home)

    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == str(good / "node")


def test_no_suitable_node_anywhere_fails_loudly(tmp_path):
    path_dir = fake_node(tmp_path / "usr-bin", "v20.20.2")
    home = tmp_path / "home"
    fake_node(home / ".nvm/versions/node/v18.0.0/bin", "v18.0.0")

    res = run_resolver(tmp_path, path_dirs=[path_dir], home=home)

    assert res.returncode != 0


def test_no_node_at_all_fails_rather_than_crashing(tmp_path):
    home = tmp_path / "home"
    home.mkdir()

    res = run_resolver(tmp_path, path_dirs=[], home=home)

    assert res.returncode != 0


def test_an_unrunnable_node_is_treated_as_absent(tmp_path):
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
    path_dir = fake_node(tmp_path / "usr-bin", "v20.20.2")
    home = tmp_path / "home"
    home.mkdir()

    res = run_resolver(tmp_path, path_dirs=[path_dir], home=home,
                       env={"TB_NODE_MIN_MAJOR": "20"})

    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == str(path_dir / "node")
