"""tools/pg.py with docker replaced by a recorder: the first run pulls the image under its own long timeout, a missing docker is a plain message, and a started container is probed."""
from __future__ import annotations

import subprocess

import pytest

import pg


class FakeDocker:
    def __init__(self, image_present=True, container_exists=False, pull_ok=True):
        self.calls: list[tuple[tuple, float]] = []
        self.image_present, self.container_exists, self.pull_ok = image_present, container_exists, pull_ok

    def __call__(self, cmd, capture_output, text, timeout):
        args = tuple(cmd[1:])
        self.calls.append((args, timeout))
        ok = subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        no = subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no such thing")
        if args[0] == "inspect":
            return subprocess.CompletedProcess(cmd, 0, stdout="true\n", stderr="") if self.container_exists else no
        if args[:2] == ("image", "inspect"):
            return ok if self.image_present else no
        if args[0] == "pull":
            return ok if self.pull_ok else subprocess.CompletedProcess(cmd, 1, stdout="", stderr="denied: pull access")
        if args[0] == "exec":
            return subprocess.CompletedProcess(cmd, 0, stdout="1\n", stderr="")
        return ok


@pytest.fixture()
def fake(monkeypatch, tmp_path):
    monkeypatch.setattr(pg, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(pg.time, "sleep", lambda s: None)

    def install(**kw):
        d = FakeDocker(**kw)
        monkeypatch.setattr(pg.subprocess, "run", d)
        return d

    return install


def test_the_first_run_pulls_the_image_with_its_own_long_timeout_and_says_so(fake, capsys):
    d = fake(image_present=False)
    pg.up()
    pulls = [(a, t) for a, t in d.calls if a[0] == "pull"]
    assert pulls == [(("pull", pg.IMAGE), pg.PULL_TIMEOUT)] and pg.PULL_TIMEOUT >= 600
    assert all(t == 180 for a, t in d.calls if a[0] != "pull")
    assert "pulling" in capsys.readouterr().out
    assert [a[0] for a, _ in d.calls].index("pull") < [a[0] for a, _ in d.calls].index("run")


def test_an_image_that_is_already_there_is_not_pulled(fake):
    d = fake(image_present=True)
    pg.up()
    assert not [a for a, _ in d.calls if a[0] == "pull"] and [a for a, _ in d.calls if a[0] == "run"]


def test_a_failed_pull_stops_with_the_reason_and_starts_nothing(fake):
    d = fake(image_present=False, pull_ok=False)
    with pytest.raises(SystemExit, match="docker pull failed: denied: pull access"):
        pg.up()
    assert not [a for a, _ in d.calls if a[0] == "run"]


def test_a_missing_docker_is_a_plain_message_and_not_a_traceback(monkeypatch, tmp_path):
    monkeypatch.setattr(pg, "ENV_FILE", tmp_path / ".env")

    def gone(*a, **k):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(pg.subprocess, "run", gone)
    for action in (pg.up, pg.down):
        with pytest.raises(SystemExit, match="docker was not found on PATH"):
            action()


def test_a_container_that_already_exists_is_started_not_created(fake):
    d = fake(container_exists=True)
    (pg.ENV_FILE).write_text("".join(f"{v}=x\n" for v in pg.VARS), encoding="utf-8")
    pg.up()
    assert not [a for a, _ in d.calls if a[0] in ("run", "pull")]
