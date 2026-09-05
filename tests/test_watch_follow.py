from contextlib import nullcontext
from pathlib import Path

from rich.layout import Layout

import nate_git_extras.watch as watch_module
from nate_git_extras.recent import RecentCommit


def _commit(sha: str, timestamp: int) -> RecentCommit:
    return RecentCommit(sha * 40, timestamp, "agent", sha)


def test_watch_follow_stays_on_newest_commit(monkeypatch) -> None:
    old_top = _commit("a", 2)
    older = _commit("b", 1)
    new_top = _commit("c", 3)
    initial = [old_top, older]
    refreshed = [new_top, old_top, older]

    monkeypatch.setattr(
        watch_module,
        "collect_remote_commits",
        lambda *_args, **_kwargs: (Path("."), initial),
    )
    monkeypatch.setattr(
        watch_module,
        "_watch_terminal",
        lambda _console: nullcontext(0),
    )
    keys = iter([None, "q"])
    monkeypatch.setattr(
        watch_module,
        "_read_key",
        lambda _fd, _timeout: next(keys),
    )
    polls = iter([(Path("."), refreshed), None])
    monkeypatch.setattr(
        watch_module,
        "_poll_scan",
        lambda _scan: next(polls),
    )

    states: list[tuple[int, bool]] = []

    def feed_layout(*_args, selected: int, following: bool, **_kwargs) -> Layout:
        states.append((selected, following))
        return Layout()

    monkeypatch.setattr(watch_module, "_feed_layout", feed_layout)

    watch_module.watch_remote("origin", follow=True)

    assert states[0] == (0, True)
    assert states[-1] == (0, True)


def test_watch_arrow_navigation_disables_follow(monkeypatch) -> None:
    commits = [_commit("a", 2), _commit("b", 1)]
    monkeypatch.setattr(
        watch_module,
        "collect_remote_commits",
        lambda *_args, **_kwargs: (Path("."), commits),
    )
    monkeypatch.setattr(
        watch_module,
        "_watch_terminal",
        lambda _console: nullcontext(0),
    )
    keys = iter(["down", "q"])
    monkeypatch.setattr(
        watch_module,
        "_read_key",
        lambda _fd, _timeout: next(keys),
    )

    states: list[tuple[int, bool]] = []

    def feed_layout(*_args, selected: int, following: bool, **_kwargs) -> Layout:
        states.append((selected, following))
        return Layout()

    monkeypatch.setattr(watch_module, "_feed_layout", feed_layout)

    watch_module.watch_remote("origin", follow=True)

    assert states[-1] == (1, False)
