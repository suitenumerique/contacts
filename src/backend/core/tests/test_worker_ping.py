"""Tests for the worker healthcheck (``python worker.py --ping``)."""

import socket
import sys
import threading
from unittest import mock

import dramatiq
import pytest
import redis

import worker
from contacts import health


def run_ping():
    """Run ``worker.py --ping`` and return its exit code."""
    with (
        mock.patch.object(sys, "argv", ["worker.py", "--ping"]),
        mock.patch("dramatiq.cli.main") as start_worker,
        pytest.raises(SystemExit) as exit_info,
    ):
        worker.main()
    start_worker.assert_not_called()
    return exit_info.value.code


def test_ping_alive(caplog):
    """--ping exits 0 when the worker answers. The eager broker answers."""
    assert run_ping() == 0
    assert not caplog.records


def test_ping_without_answer(caplog):
    """--ping exits 1 when no worker consumes the ping."""
    broker = dramatiq.get_broker()
    with (
        mock.patch.object(broker, "enqueue", side_effect=lambda message, **_: message),
        mock.patch.object(health, "PING_TIMEOUT", 50),
    ):
        assert run_ping() == 1
    assert "ResultTimeout" in caplog.text


def test_ping_with_wrong_answer(caplog):
    """--ping exits 1 when the stored result is not the answer of the actor."""
    with mock.patch.object(health.ping, "fn", return_value=None):
        assert run_ping() == 1
    # No error: the result is there, but it is not "pong".
    assert not caplog.records


def test_ping_broker_down(caplog):
    """--ping exits 1 when the broker refuses the ping."""
    broker = dramatiq.get_broker()
    with mock.patch.object(broker, "enqueue", side_effect=redis.ConnectionError):
        assert run_ping() == 1
    assert "ConnectionError" in caplog.text


def test_ping_call_never_returns(caplog):
    """--ping exits 1 when a call to Redis never returns."""
    broker = dramatiq.get_broker()
    never = threading.Event()
    with (
        mock.patch.object(broker, "enqueue", side_effect=lambda *_, **__: never.wait()),
        mock.patch.object(health, "PROBE_DEADLINE", 100),
    ):
        assert run_ping() == 1
    assert "did not finish" in caplog.text


def test_worker_consumes_its_ping_queue():
    """The worker consumes the ping queue of its container with the other queues."""
    assert health.PING_QUEUE == f"ping.{socket.gethostname()}"
    assert health.ping.queue_name == health.PING_QUEUE

    dramatiq_argv = []
    with (
        mock.patch.object(sys, "argv", ["worker.py", "--queues=import"]),
        mock.patch(
            "dramatiq.cli.main", side_effect=lambda: dramatiq_argv.extend(sys.argv)
        ),
    ):
        worker.main()

    queues = dramatiq_argv[dramatiq_argv.index("--queues") + 1 :]
    assert queues == ["import", health.PING_QUEUE]
    assert "contacts.health" in dramatiq_argv


def test_ping_queue_deleted_on_shutdown():
    """The worker deletes its ping queue when it stops."""
    broker = mock.Mock()
    health.DropPingQueue().after_worker_shutdown(broker, worker=None)
    broker.flush.assert_called_once_with(health.PING_QUEUE)


def test_ping_queue_cleanup_survives_redis_outage():
    """A Redis failure during the cleanup does not break the shutdown."""
    broker = mock.Mock()
    broker.flush.side_effect = redis.ConnectionError
    health.DropPingQueue().after_worker_shutdown(broker, worker=None)
