from contextlib import nullcontext
from pathlib import Path
import threading
import time

import nate_git_extras.status as status


def test_status_rescan_runs_off_ui_thread(tmp_path: Path, monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()
    expected = (tmp_path, [])

    def slow_snapshot(*args, **kwargs):
        started.set()
        release.wait(timeout=1)
        return expected

    monkeypatch.setattr(status, "_snapshot", slow_snapshot)
    scan = status.ScanStatus()

    before = time.monotonic()
    assert status._start_scan(
        tmp_path,
        base="master",
        stale_days=14,
        include_remotes=False,
        scan=scan,
    )
    elapsed = time.monotonic() - before

    assert elapsed < 0.1
    assert started.wait(timeout=0.5)
    assert status._poll_scan(scan) is None

    release.set()
    assert scan.thread is not None
    scan.thread.join(timeout=0.5)
    assert status._poll_scan(scan) == expected


def test_watch_without_interval_never_fetches_automatically(tmp_path: Path, monkeypatch) -> None:
    fetches = 0
    keys = iter([None, None, "q"])

    def start_fetch(*args, **kwargs) -> bool:
        nonlocal fetches
        fetches += 1
        return True

    monkeypatch.setattr(status, "_snapshot", lambda *args, **kwargs: (tmp_path, []))
    monkeypatch.setattr(status, "_watch_terminal", lambda _console: nullcontext(0))
    monkeypatch.setattr(status, "_watch_key", lambda _fd, _timeout: next(keys))
    monkeypatch.setattr(status, "_start_fetch", start_fetch)

    status.print_branch_status(tmp_path, watch=True)

    assert fetches == 0


def test_manual_fetch_refreshes_remotes_once(tmp_path: Path, monkeypatch) -> None:
    requests: list[bool] = []
    keys = iter(["g", None, None, None, "q"])
    remote = status.BranchStatus(
        "origin/agent", 1, 0, 1, "READY", "ff", False, None, False, True
    )

    class FakeLive:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def update(self, *args, **kwargs):
            pass

    def start_fetch(_root: Path, fetch: status.FetchStatus) -> bool:
        fetch.state = "fetching"
        fetch.process = object()
        return True

    def poll_fetch(fetch: status.FetchStatus) -> bool:
        if fetch.state != "fetching":
            return False
        fetch.state = "ok"
        fetch.process = None
        return True

    def start_scan(*args, include_remotes: bool, scan: status.ScanStatus, **kwargs) -> bool:
        requests.append(include_remotes)
        scan.include_remotes = include_remotes
        scan.thread = object()
        scan.result = (tmp_path, [remote] if include_remotes else [])
        return True

    def poll_scan(scan: status.ScanStatus):
        if scan.thread is None:
            return None
        scan.thread = None
        result = scan.result
        scan.result = None
        return result

    monkeypatch.setattr(status, "Live", FakeLive)
    monkeypatch.setattr(status, "_WATCH_REFRESH_SECONDS", 0.0)
    monkeypatch.setattr(status, "_snapshot", lambda *args, **kwargs: (tmp_path, []))
    monkeypatch.setattr(status, "_watch_terminal", lambda _console: nullcontext(0))
    monkeypatch.setattr(status, "_watch_key", lambda _fd, _timeout: next(keys))
    monkeypatch.setattr(status, "_start_fetch", start_fetch)
    monkeypatch.setattr(status, "_poll_fetch", poll_fetch)
    monkeypatch.setattr(status, "_start_scan", start_scan)
    monkeypatch.setattr(status, "_poll_scan", poll_scan)
    monkeypatch.setattr(status, "_stop_fetch", lambda _fetch: None)

    status.print_branch_status(tmp_path, watch=True)

    assert requests == [True, False]
