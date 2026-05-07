"""Tests for gunicorn_conf.py (M6+M7).

The config itself is data — the only behaviour worth testing is the
`child_exit` hook that wires prometheus_client's per-pid cleanup. Without it,
/tmp/prom_multiproc accumulates orphan Gauge files forever.
"""

from types import SimpleNamespace
from unittest.mock import patch


def test_module_importable():
    """Smoke: gunicorn_conf.py must import cleanly with the project on PYTHONPATH."""
    import gunicorn_conf

    assert gunicorn_conf.workers >= 1
    assert gunicorn_conf.worker_class == "uvicorn.workers.UvicornWorker"
    assert gunicorn_conf.max_requests >= 1
    assert gunicorn_conf.max_requests_jitter >= 0
    assert gunicorn_conf.preload_app is True
    # graceful_timeout must match app/lifecycle.py SHUTDOWN_TIMEOUT.
    from app.lifecycle import SHUTDOWN_TIMEOUT

    assert gunicorn_conf.graceful_timeout == SHUTDOWN_TIMEOUT


def test_child_exit_marks_worker_pid_dead():
    """The hook must call prometheus_client.multiprocess.mark_process_dead with the worker's pid."""
    import gunicorn_conf

    with patch("prometheus_client.multiprocess.mark_process_dead") as mark_dead:
        gunicorn_conf.child_exit(server=None, worker=SimpleNamespace(pid=12345))
        mark_dead.assert_called_once_with(12345)
