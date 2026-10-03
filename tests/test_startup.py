"""Serving start-up: gunicorn initialises every worker, one scheduler runs jobs."""
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_only_one_scheduler_leads(app, tmp_path):
    from module_manager import TaskScheduler

    lock = str(tmp_path / 'scheduler.lock')
    first, second = TaskScheduler(app, lock), TaskScheduler(app, lock)
    assert first._try_lead() is True
    assert second._try_lead() is False
    assert first.is_leader and not second.is_leader

    first._release_lead()  # leader exits → another worker takes over
    assert second._try_lead() is True
    second._release_lead()


@pytest.mark.parametrize('lead', [True, False])
def test_scheduler_loop_runs_jobs_only_as_leader(app, tmp_path, monkeypatch, lead):
    from module_manager import TaskScheduler

    lock = str(tmp_path / 'scheduler.lock')
    other = TaskScheduler(app, lock)
    if not lead:
        assert other._try_lead()  # another worker already leads
    sched = TaskScheduler(app, lock)
    runs = []
    sched.add_job('t.job', lambda: runs.append(1), job_type='interval',
                  interval=0)
    sched._running = True
    # Stop after one loop iteration instead of sleeping 30 s.
    monkeypatch.setattr(time, 'sleep', lambda _s: setattr(sched, '_running', False))
    sched._loop()
    assert runs == ([1] if lead else [])
    assert not sched.is_leader  # leadership released when the loop exits
    other._release_lead()


def _free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _serve(tmp_path, workers):
    """Start gunicorn the way the image does; return (log text, /setup status)."""
    pytest.importorskip('gunicorn')
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if k != 'GUNICORN_WORKERS'}
    env.update(SECRET_KEY='test-only-not-secret', FLASK_DEBUG='0',
               DATABASE_URL=f'sqlite:///{tmp_path / "app.db"}',
               AUTH_CONFIG_PATH=str(tmp_path / 'auth_config.json'),
               SCHEDULER_LOCK_PATH=str(tmp_path / 'scheduler.lock'),
               GUNICORN_BIND=f'127.0.0.1:{port}')
    if workers:
        env['GUNICORN_WORKERS'] = workers
    log = tmp_path / 'gunicorn.log'
    with open(log, 'w') as out:
        proc = subprocess.Popen(
            [sys.executable, '-m', 'gunicorn', 'app:app'], cwd=ROOT, env=env,
            stdout=out, stderr=subprocess.STDOUT)
    try:
        status = None
        for _ in range(60):
            time.sleep(0.5)
            try:
                status = urllib.request.urlopen(
                    f'http://127.0.0.1:{port}/setup', timeout=2).status
                break
            except Exception:
                continue
        time.sleep(1.5)  # let every worker finish post_worker_init
    finally:
        proc.terminate()
        proc.wait(timeout=30)
    return log.read_text(), status


@pytest.mark.skipif(os.name != 'posix', reason='gunicorn needs POSIX')
@pytest.mark.parametrize('workers,booted', [(None, 1), ('2', 2)])
def test_gunicorn_initialises_workers_and_one_scheduler(tmp_path, workers, booted):
    text, status = _serve(tmp_path, workers)
    assert status == 200, text[-3000:]
    assert text.count('Booting worker') == booted, text[-3000:]
    # bootstrap() ran without GUNICORN_WORKERS, and only one process leads.
    assert text.count('Scheduler: this process') == 1, text[-3000:]
