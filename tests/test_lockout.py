"""Account-lockout helper tests."""
import auth_routes as ar


def test_lockout_after_threshold():
    ar._failed_attempts.clear()
    ip = '203.0.113.7'
    for _ in range(ar._LOCK_THRESHOLD):
        assert not ar._is_locked(ip)
        ar._record_failure(ip)
    assert ar._is_locked(ip)


def test_reset_clears_lock():
    ar._failed_attempts.clear()
    ip = '203.0.113.8'
    for _ in range(ar._LOCK_THRESHOLD):
        ar._record_failure(ip)
    assert ar._is_locked(ip)
    ar._reset_failures(ip)
    assert not ar._is_locked(ip)


def test_window_expiry(monkeypatch):
    ar._failed_attempts.clear()
    ip = '203.0.113.9'
    base = 1_000_000.0
    monkeypatch.setattr(ar._time, 'time', lambda: base)
    for _ in range(ar._LOCK_THRESHOLD):
        ar._record_failure(ip)
    assert ar._is_locked(ip)
    # Jump past the lock window — lock should clear.
    monkeypatch.setattr(ar._time, 'time', lambda: base + ar._LOCK_WINDOW + 1)
    assert not ar._is_locked(ip)
