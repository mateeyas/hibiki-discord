"""
Deduplication and rate limiting for Discord webhook sends.

Discord rate limits webhooks at roughly 5 requests per 2 seconds and
returns 429 beyond that. Without throttling, a caller stuck in a retry
loop produces one webhook request per attempt, exceeds the limit, and
loses notifications silently, because send failures are logged rather
than raised.

Two mechanisms, in order:

1. Deduplication collapses identical notifications inside a window.
2. A send budget backstops the rest, for the case dedup cannot catch --
   distinct messages arriving in bulk.

Notifications beyond the budget are dropped rather than queued, and the
number dropped is reported on the next send. Queueing would buy
completeness at the cost of a background worker and its lifecycle, which
is more machinery than this package wants.

State is a dict and a list of timestamps per webhook, inspected
synchronously on the existing send path. There is no background task to
start, drain, or shut down.

This mirrors ``hibiki_logger.throttle``, with one deliberate difference:
that package derives its dedup signature from the traceback, because log
messages embed request ids and would otherwise never collapse. Business
notifications carry no traceback, so the signature here is the
notification type plus the rendered message -- only genuinely identical
notifications collapse. Set ``dedup_window = 0`` for a notification type
whose every occurrence is a distinct event worth its own message.
"""

import logging
import time
from typing import Callable, Optional, Tuple

logger = logging.getLogger("hibiki_discord")

# Upper bound on tracked dedup signatures, so a pathological spread of
# distinct notifications cannot grow the table without limit.
MAX_TRACKED_SIGNATURES = 512

_WINDOW_SECONDS = 60.0


class ThrottleDecision:
    """Outcome of a throttle check for one notification.

    A decision to send reserves a dedup window and a budget slot. If the
    send then fails, pass the decision to ``record_failure`` so the window
    is released; see that method for why the budget slot is not.
    """

    __slots__ = ("send", "suppressed", "dropped", "_key", "_previous_window")

    def __init__(self, send: bool, suppressed: int = 0, dropped: int = 0):
        self.send = send
        # Occurrences of this signature collapsed since it was last sent.
        self.suppressed = suppressed
        # Notifications discarded for exceeding the send budget since the
        # last send on this webhook.
        self.dropped = dropped
        self._key = None
        self._previous_window = None

    def __repr__(self) -> str:
        return (
            f"ThrottleDecision(send={self.send}, suppressed={self.suppressed}, "
            f"dropped={self.dropped})"
        )


class _Window:
    """One dedup signature: when its window opened, what it has absorbed.

    ``length`` is the dedup window this signature was opened under. It is
    stored per window rather than read from the caller because the window
    is configurable per notification type: a send from a type running
    without dedup must not expire the window of one running with it.
    """

    __slots__ = ("opened_at", "suppressed", "length")

    def __init__(self, opened_at: float, length: float):
        self.opened_at = opened_at
        self.suppressed = 0
        self.length = length


def signature(notification_type: str, message: str) -> Tuple[str, str]:
    """Derive the dedup signature for a notification."""
    return (notification_type or "", message or "")


class DiscordThrottle:
    """Decides whether a notification should reach the webhook.

    ``check`` touches no network, takes no locks, and never blocks. It
    returns the counts the caller should surface in the message it sends.

    The send budget is tracked per webhook URL, so a burst on one
    notification's webhook cannot shed another's.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._windows: dict = {}
        self._sends: dict = {}
        self._dropped_pending: dict = {}

    def check(
        self,
        notification_type: str,
        message: str,
        webhook_url: str,
        dedup_window: int,
        max_per_minute: int,
    ) -> ThrottleDecision:
        """Decide whether to send this notification, and with what counts."""
        now = self._clock()
        max_per_minute = max(1, max_per_minute)
        key = (webhook_url, signature(notification_type, message))
        window = self._windows.get(key)

        if (
            dedup_window > 0
            and window is not None
            and now - window.opened_at < dedup_window
        ):
            window.suppressed += 1
            return ThrottleDecision(False)

        # The window has expired, or this signature is new. Anything the
        # expired window absorbed is reported on this send.
        pending = window.suppressed if window is not None else 0

        if not self._has_budget(webhook_url, now, max_per_minute):
            # Shed the notification but keep the window, so its suppression
            # count is not lost, and count the drop for the next send.
            self._dropped_pending[webhook_url] = (
                self._dropped_pending.get(webhook_url, 0) + 1
            )
            return ThrottleDecision(False)

        self._sweep(now)
        self._windows[key] = _Window(now, dedup_window)
        self._sends.setdefault(webhook_url, []).append(now)

        dropped = self._dropped_pending.pop(webhook_url, 0)

        decision = ThrottleDecision(True, suppressed=pending, dropped=dropped)
        decision._key = key
        decision._previous_window = window
        return decision

    def record_failure(self, decision: ThrottleDecision) -> None:
        """Release the dedup window reserved by a send that did not land.

        Without this, a webhook that is briefly unreachable silences the
        notification for the whole dedup window: the first send opens the
        window, fails to deliver, and every later occurrence is collapsed
        into a message nobody received. So a failed send must not count as
        a send.

        The budget slot is deliberately *not* released. Retrying against a
        webhook that is down should stay bounded, and letting failures
        consume budget caps the attempts at ``max_per_minute`` rather than
        one per occurrence.
        """
        if not decision.send or decision._key is None:
            return

        webhook_url = decision._key[0]

        # Occurrences that arrived while the send was in flight landed on
        # the window the send opened. They were neither delivered nor
        # reported, so they have to survive the rollback.
        in_flight = self._windows.get(decision._key)
        carried = in_flight.suppressed if in_flight is not None else 0

        if decision._previous_window is not None:
            decision._previous_window.suppressed += carried
            self._windows[decision._key] = decision._previous_window
        elif carried:
            # No earlier window to restore. Backdate this one so it counts
            # as expired and the next occurrence sends and reports them.
            in_flight.opened_at = self._clock() - in_flight.length
        else:
            self._windows.pop(decision._key, None)

        # Counts were consumed by a notification that never arrived; report
        # them on whichever send lands next.
        if decision.dropped:
            self._dropped_pending[webhook_url] = (
                self._dropped_pending.get(webhook_url, 0) + decision.dropped
            )

    def _has_budget(self, webhook_url: str, now: float, max_per_minute: int) -> bool:
        """True when a send now stays inside the sliding-window budget."""
        cutoff = now - _WINDOW_SECONDS
        sends = self._sends.get(webhook_url)
        if not sends:
            return True
        # Sends are appended in order, so dropping the stale head suffices.
        # The list never exceeds the budget, so this stays cheap.
        while sends and sends[0] <= cutoff:
            sends.pop(0)
        return len(sends) < max_per_minute

    def _sweep(self, now: float) -> None:
        """Discard expired windows that have nothing left to report.

        Each window is judged against its own length, not the length of
        whichever notification happens to be sending. Windows holding a
        suppression count are kept until that count is delivered. Beyond a
        hard cap the oldest are discarded regardless, so a pathological
        spread of distinct notifications cannot grow the table without
        limit.
        """
        if len(self._windows) < MAX_TRACKED_SIGNATURES:
            expired = [
                key
                for key, window in self._windows.items()
                if window.suppressed == 0 and now - window.opened_at >= window.length
            ]
            for key in expired:
                del self._windows[key]
            return

        ordered = sorted(self._windows.items(), key=lambda item: item[1].opened_at)
        for key, _ in ordered[: len(self._windows) - MAX_TRACKED_SIGNATURES + 1]:
            del self._windows[key]

    def reset(self) -> None:
        """Clear all throttle state. Intended for use in tests."""
        self._windows.clear()
        self._sends.clear()
        self._dropped_pending.clear()


_throttle: Optional[DiscordThrottle] = None


def get_throttle() -> DiscordThrottle:
    """Return the process-wide throttle, creating it on first use."""
    global _throttle
    if _throttle is None:
        _throttle = DiscordThrottle()
    return _throttle


def reset_throttle() -> None:
    """Discard deduplication and rate-limit state.

    Called when config is reloaded so new settings start from a clean
    slate, and useful in tests to keep one case's sends from suppressing
    the next one's.
    """
    global _throttle
    _throttle = None
