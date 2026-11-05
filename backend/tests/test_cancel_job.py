"""Canceling a running job stops it where it is, including a helper it's waiting on."""

import subprocess
import time

import pytest

from steno.ingestion import worker
from steno.ingestion.queue import JobCanceled
from steno.ingestion.worker import cancellable


def test_a_cancel_stops_python_work_mid_stage(monkeypatch):
    monkeypatch.setattr(worker, "CANCEL_POLL_S", 0.05)
    started = time.monotonic()
    with pytest.raises(JobCanceled), cancellable(1, lambda _: True):
        while time.monotonic() - started < 10:
            pass
    assert time.monotonic() - started < 5


def test_a_cancel_kills_the_helper_a_job_waits_on(monkeypatch):
    monkeypatch.setattr(worker, "CANCEL_POLL_S", 0.05)
    marker = "98.7654321"  # an unusual sleep, so only this test's process matches
    started = time.monotonic()
    with pytest.raises(JobCanceled), cancellable(1, lambda _: True):
        subprocess.run(["sleep", marker], check=False)
    assert time.monotonic() - started < 5
    left = subprocess.run(["pgrep", "-f", f"sleep {marker}"], capture_output=True, text=True)
    assert left.stdout.strip() == ""


def test_no_cancel_leaves_the_job_alone(monkeypatch):
    monkeypatch.setattr(worker, "CANCEL_POLL_S", 0.01)
    with cancellable(1, lambda _: False):
        time.sleep(0.05)


def test_a_cancel_can_arrive_only_while_a_job_runs(monkeypatch):
    monkeypatch.setattr(worker, "CANCEL_POLL_S", 0.01)
    with cancellable(1, lambda _: False):
        pass
    time.sleep(0.05)  # no signal is sent later, which would have killed this process
