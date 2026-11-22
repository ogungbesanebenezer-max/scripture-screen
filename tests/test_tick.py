"""The UI loop runs for a whole service, so one bad event must not end it."""
import queue
from types import SimpleNamespace

from verse_screen.app import App


def make_app(handle_event, auto_expiry=lambda: None):
    scheduled = []
    app = SimpleNamespace(
        events=queue.Queue(), level={}, ring=SimpleNamespace(level=0.0), root=SimpleNamespace(after=lambda *a: scheduled.append(a)),
        _handle_event=handle_event, _auto_expiry=auto_expiry)
    app._tick = lambda: App._tick(app)
    return app, scheduled


def test_bad_event_does_not_stop_later_events_or_the_loop():
    handled = []

    def handle(name, _value):
        if name == "bad":
            raise RuntimeError("boom")
        handled.append(name)

    app, scheduled = make_app(handle)
    app.events.put(("bad", None))
    app.events.put(("status", "ok"))
    App._tick(app)
    assert handled == ["status"]
    assert len(scheduled) == 1


def test_failure_after_the_events_still_reschedules():
    def fail():
        raise RuntimeError("boom")

    app, scheduled = make_app(lambda *_: None, auto_expiry=fail)
    App._tick(app)
    assert len(scheduled) == 1
