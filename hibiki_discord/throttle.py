"""
Deduplication and rate limiting for Discord webhook sends.

Discord rate limits webhooks at roughly 5 requests per 2 seconds and
returns 429 beyond that. Without throttling, a caller stuck in a retry
loop produces one webhook request per attempt, exceeds the limit, and
loses notifications silently, because send failures are logged rather
than raised.

Three mechanisms, in order:

1. Deduplication collapses identical notifications inside a window.
2. A send budget backstops the rest, for the case dedup cannot catch --
   distinct messages arriving in bulk.
3. Pacing spaces out what survives, because the budget bounds the average
   over a minute and Discord's limit is on the instant: thirty sends are
   inside a budget of thirty per minute whether they arrive spread out or
   all together, and all together is exactly what trips the limit.

Notifications beyond the budget are dropped rather than queued, and the
number dropped is reported on the next send. Queueing would buy
completeness at the cost of a background worker and its lifecycle, which
is more machinery than this package wants. Pacing is not queueing: each
caller waits for its own turn on its own task, and there is still nothing
to start or drain.

Every webhook request is charged to the budget, retries included. A send
that is retried four times against a failing webhook costs four slots, not
one, so an incident cannot quietly multiply the request rate by four.

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

# Discord allows roughly 5 requests per 2 seconds. Half a second between
# sends on one webhook stays under that with room for the retries that
# share the same budget.
MIN_SEND_SPACING_SECONDS = 0.5

# How far ahead pacing will schedule before shedding instead. A caller
# should not be held for longer than the budget window it is being paced
# within; beyond this the notification is dropped and counted like any
# other over-budget send.
MAX_PACING_DELAY_SECONDS = _WINDOW_SECONDS


class ThrottleDecision:
    """Outcome of a throttle check for one notification.

    A decision to send reserves a dedup window and a budget slot. If the
    send then fails, pass the decision to ``record_failure`` so the window
    is released; see that method for why the budget slot is not.

    ``delay`` is how long the caller must wait before making the request.
    It is the caller's job to honour it -- the throttle never sleeps -- and
    ignoring it gives back exactly the burst pacing exists to prevent.
    """

    __slots__ = (
        "send", "suppressed", "dropped", "delay", "_key", "_previous_window"
    )

    def __init__(
        self,
        send: bool,
        suppressed: int = 0,
        dropped: int = 0,
        delay: float = 0.0,
    ):
        self.send = send
        # Seconds to wait before the request, so sends that would leave
        # together are spaced instead.
        self.delay = delay
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
            f"dropped={self.dropped}, delay={self.delay:.2f})"
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

        scheduled = self._next_slot(webhook_url, now)
        if scheduled - now > MAX_PACING_DELAY_SECONDS:
            # The paced queue on this webhook is already a minute deep.
            # Holding the caller longer than the budget window it is being
            # paced within is worse than shedding, so shed and count it.
            self._dropped_pending[webhook_url] = (
                self._dropped_pending.get(webhook_url, 0) + 1
            )
            return ThrottleDecision(False)

        self._sweep(now)
        self._windows[key] = _Window(now, dedup_window)
        self._sends.setdefault(webhook_url, []).append(scheduled)

        dropped = self._dropped_pending.pop(webhook_url, 0)

        decision = ThrottleDecision(
            True, suppressed=pending, dropped=dropped, delay=scheduled - now
        )
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

    def record_retries(self, webhook_url: str, extra_requests: int) -> None:
        """Charge a send's retry attempts to the webhook's budget.

        ``check`` reserves one slot per notification, but a send retries up
        to four times, so during a Discord incident a budget of thirty
        notifications a minute would otherwise permit a hundred and twenty
        requests. Bounding the request rate is what the budget is for, so
        the attempts beyond the first are charged here once the send is
        done and their number is known.

        They are timestamped with the send that made them, which keeps the
        list ordered for ``_has_budget``.
        """
        if extra_requests <= 0:
            return
        sends = self._sends.setdefault(webhook_url, [])
        at = sends[-1] if sends else self._clock()
        sends.extend([at] * extra_requests)

    def _next_slot(self, webhook_url: str, now: float) -> float:
        """When the next send on this webhook may go out.

        Sends are spaced by MIN_SEND_SPACING_SECONDS, measured from the
        last slot handed out rather than from the last send that actually
        happened, so a batch arriving in one instant is spread rather than
        all being told to go now.
        """
        sends = self._sends.get(webhook_url)
        if not sends:
            return now
        return max(now, sends[-1] + MIN_SEND_SPACING_SECONDS)

    def _has_budget(self, webhook_url: str, now: float, max_per_minute: int) -> bool:
        """True when a send now stays inside the sliding-window budget."""
        cutoff = now - _WINDOW_SECONDS
        sends = self._sends.get(webhook_url)
        if not sends:
            return True
        # Slots are appended in order -- scheduled times from _next_slot,
        # retries timestamped with the send they belong to -- so dropping
        # the stale head suffices. Retries can push the list past the
        # budget, which is the point: they are requests too.
        while sends and sends[0] <= cutoff:
            sends.pop(0)
        return len(sends) < max_per_minute

    def _sweep(self, now: float) -> None:
        """Discard expired windows that have nothing left to report.

        Each window is judged against its own length, not the length of
        whichever notification happens to be sending. Windows holding a
        suppression count are kept until that count is delivered. Beyond a
        hard cap they are discarded regardless, so a pathological spread of
        distinct notifications cannot grow the table without limit.

        Expiry runs on every send, including once the table is full. It
        used to be skipped at the cap, which pinned the table there for
        good: an expired, empty window was never reclaimed, and each send
        evicted one live entry to make room.

        Eviction order matters. Windows with nothing to report go first,
        oldest first among them, and only then windows still holding a
        count -- which is the opposite of oldest-first, because the oldest
        window is the long dedup window most likely to be holding the
        largest count. Dropping a count loses the "N further occurrences
        suppressed" note for good, so the few that are dropped are logged.
        """
        expired = [
            key
            for key, window in self._windows.items()
            if window.suppressed == 0 and now - window.opened_at >= window.length
        ]
        for key in expired:
            del self._windows[key]

        # Leave room for the window the caller is about to open.
        overflow = len(self._windows) - (MAX_TRACKED_SIGNATURES - 1)
        if overflow <= 0:
            return

        ordered = sorted(
            self._windows.items(),
            key=lambda item: (item[1].suppressed > 0, item[1].opened_at),
        )
        lost = 0
        for key, window in ordered[:overflow]:
            lost += window.suppressed
            del self._windows[key]
        if lost:
            logger.warning(
                "Discord throttle is tracking %d signatures; discarded %d "
                "suppressed occurrence(s) that will not be reported",
                MAX_TRACKED_SIGNATURES,
                lost,
            )

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
