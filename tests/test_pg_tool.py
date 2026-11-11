"""tools/pg.py with docker replaced by a recorder: the first run pulls the image under its own long timeout, a missing docker is a plain message, and a started container is probed."""
from __future__ import annotations

import json
import subprocess

import pytest

import pg


class FakeDocker:
    def __init__(self, image_present=True, container_exists=False, pull_ok=True, host_ip="127.0.0.1"):
        self.calls: list[tuple[tuple, float]] = []
        self.image_present, self.container_exists, self.pull_ok, self.host_ip = image_present, container_exists, pull_ok, host_ip

    def __call__(self, cmd, capture_output, text, timeout):
        args = tuple(cmd[1:])
        self.calls.append((args, timeout))
        if args[0] == "rm":
            self.container_exists = False                                                          # a removed container is gone for the next question
        ok = subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        no = subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no such thing")
        if args[0] == "inspect" and "{{json .HostConfig.PortBindings}}" in args:
            bindings = {"5432/tcp": [{"HostIp": self.host_ip, "HostPort": "5436"}]}
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(bindings) + "\n", stderr="") if self.container_exists else no
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


# ---- the database is published on this machine only (P5-17) ---------------------------------------------------------------------------------------------

def run_calls(d):
    return [a for a, _ in d.calls if a[0] == "run"]


def test_the_container_is_published_on_127_0_0_1_only(fake):
    d = fake()
    pg.up()
    (run,) = run_calls(d)
    assert run[run.index("-p") + 1] == "127.0.0.1:5436:5432" and pg.PUBLISH == "127.0.0.1:5436:5432"
    assert [a for a in run if a.endswith(":5432")] == ["127.0.0.1:5436:5432"]                    # no other mapping, and none without the host address


@pytest.mark.parametrize("host_ip, replaced", [("0.0.0.0", True), ("", True), ("::", True), ("192.168.1.5", True), ("127.0.0.1", False)])
def test_class_a_container_published_beyond_this_machine_is_replaced_and_one_published_on_loopback_is_kept(fake, capsys, host_ip, replaced):
    """CLASS-TABLE: any host address other than 127.0.0.1 means the database can be reached from the network; the container is removed with its volume and created again."""
    d = fake(container_exists=True, host_ip=host_ip)
    pg.ENV_FILE.write_text("".join(f"{v}=x\n" for v in pg.VARS), encoding="utf-8")
    pg.up()
    removed = [a for a, _ in d.calls if a[:3] == ("rm", "-f", "-v")]
    assert bool(removed) is replaced and bool(run_calls(d)) is replaced
    assert ("every network interface" in capsys.readouterr().out) is replaced


def test_a_container_that_is_absent_or_answers_nonsense_is_not_replaced_for_its_ports(fake, monkeypatch):
    fake()                                                                                         # no container: nothing to ask about
    assert pg.published_beyond_loopback() is False
    d = fake(container_exists=True)
    real = pg._docker

    def answering(text):
        def garbled(*args, timeout=180):
            if "{{json .HostConfig.PortBindings}}" in args:
                return subprocess.CompletedProcess(args, 0, stdout=text, stderr="")
            return real(*args, timeout=timeout)
        return garbled

    pg.ENV_FILE.write_text("".join(f"{v}=x\n" for v in pg.VARS), encoding="utf-8")
    for text in ("not json", "true", "null", "{}", '{"5432/tcp": null}'):
        monkeypatch.setattr(pg, "_docker", answering(text))
        assert pg.published_beyond_loopback() is False, text
        pg.up()
    assert not [a for a, _ in d.calls if a[:3] == ("rm", "-f", "-v")]
