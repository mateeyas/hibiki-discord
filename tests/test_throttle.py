import logging

from hibiki_discord.throttle import (
    MAX_PACING_DELAY_SECONDS,
    MAX_TRACKED_SIGNATURES,
    MIN_SEND_SPACING_SECONDS,
    DiscordThrottle,
    signature,
)

WEBHOOK = "https://discord.com/api/webhooks/test"
OTHER_WEBHOOK = "https://discord.com/api/webhooks/other"


class FakeClock:
    """Monotonic clock under test control."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_throttle(clock=None):
    return DiscordThrottle(clock=clock or FakeClock())


def check(throttle, message="hello", ntype="signup", webhook=WEBHOOK,
          dedup_window=300, max_per_minute=30):
    return throttle.check(
        notification_type=ntype,
        message=message,
        webhook_url=webhook,
        dedup_window=dedup_window,
        max_per_minute=max_per_minute,
    )


class TestSignature:
    def test_same_type_and_message_share_a_signature(self):
        assert signature("signup", "New user: a***@x.com") == signature(
            "signup", "New user: a***@x.com"
        )

    def test_different_messages_differ(self):
        assert signature("signup", "New user: a***@x.com") != signature(
            "signup", "New user: b***@x.com"
        )

    def test_different_types_differ(self):
        assert signature("signup", "hello") != signature("subscription", "hello")


class TestDeduplication:
    def test_identical_notifications_collapse(self):
        throttle = make_throttle()
        assert check(throttle).send is True
        for _ in range(50):
            assert check(throttle).send is False

    def test_distinct_messages_are_not_collapsed(self):
        throttle = make_throttle()
        assert check(throttle, message="user 1").send is True
        assert check(throttle, message="user 2").send is True

    def test_window_reopens_after_expiry(self):
        clock = FakeClock()
        throttle = make_throttle(clock)
        assert check(throttle).send is True
        assert check(throttle).send is False

        clock.advance(301)
        decision = check(throttle)
        assert decision.send is True
        assert decision.suppressed == 1

    def test_suppressed_count_reports_the_whole_window(self):
        clock = FakeClock()
        throttle = make_throttle(clock)
        check(throttle)
        for _ in range(142):
            check(throttle)

        clock.advance(301)
        assert check(throttle).suppressed == 142

    def test_zero_window_disables_deduplication(self):
        """Every occurrence of a business event can be worth its own message."""
        throttle = make_throttle()
        for _ in range(5):
            assert check(throttle, dedup_window=0).send is True

    def test_counts_reset_after_being_reported(self):
        clock = FakeClock()
        throttle = make_throttle(clock)
        check(throttle)
        check(throttle)
        clock.advance(301)
        assert check(throttle).suppressed == 1
        clock.advance(301)
        assert check(throttle).suppressed == 0


class TestSendBudget:
    def test_budget_caps_distinct_notifications(self):
        """The case dedup cannot catch: 500 distinct messages at once."""
        throttle = make_throttle()
        sent = sum(
            1
            for i in range(500)
            if check(throttle, message=f"user {i}", max_per_minute=10).send
        )
        assert sent == 10

    def test_dropped_count_surfaces_on_the_next_send(self):
        clock = FakeClock()
        throttle = make_throttle(clock)
        for i in range(20):
            check(throttle, message=f"user {i}", max_per_minute=5)

        clock.advance(61)
        decision = check(throttle, message="user 999", max_per_minute=5)
        assert decision.send is True
        assert decision.dropped == 15

    def test_budget_is_a_sliding_window(self):
        clock = FakeClock()
        throttle = make_throttle(clock)
        for i in range(5):
            assert check(throttle, message=f"a{i}", max_per_minute=5).send is True
        assert check(throttle, message="a6", max_per_minute=5).send is False

        clock.advance(61)
        assert check(throttle, message="a7", max_per_minute=5).send is True

    def test_budget_is_per_webhook(self):
        """A burst on one webhook must not shed another's notifications."""
        throttle = make_throttle()
        for i in range(5):
            check(throttle, message=f"a{i}", max_per_minute=5)
        assert check(throttle, message="a6", max_per_minute=5).send is False
        assert check(
            throttle, message="b1", webhook=OTHER_WEBHOOK, max_per_minute=5
        ).send is True

    def test_shed_notification_keeps_its_dedup_window(self):
        """A drop must not lose the suppression count already collected."""
        clock = FakeClock()
        throttle = make_throttle(clock)
        opts = {"message": "repeat", "dedup_window": 10, "max_per_minute": 1}
        check(throttle, **opts)
        check(throttle, **opts)

        # The dedup window has expired but the budget slot has not, so this
        # occurrence is shed rather than sent.
        clock.advance(11)
        assert check(throttle, **opts).send is False

        clock.advance(61)
        decision = check(throttle, **opts)
        assert decision.send is True
        assert decision.suppressed == 1
        assert decision.dropped == 1


class TestRecordFailure:
    def test_failed_send_does_not_open_a_window(self):
        """A webhook outage must not silence the notification behind it."""
        throttle = make_throttle()
        decision = check(throttle)
        throttle.record_failure(decision)
        assert check(throttle).send is True

    def test_failed_send_still_consumes_budget(self):
        """Retrying against a dead webhook stays bounded."""
        throttle = make_throttle()
        for i in range(3):
            decision = check(throttle, message=f"m{i}", max_per_minute=3)
            throttle.record_failure(decision)
        assert check(throttle, message="m4", max_per_minute=3).send is False

    def test_failed_send_restores_a_previous_suppression_count(self):
        clock = FakeClock()
        throttle = make_throttle(clock)
        check(throttle)
        check(throttle)
        clock.advance(301)

        decision = check(throttle)
        assert decision.suppressed == 1
        throttle.record_failure(decision)

        # The count was consumed by a message nobody received, so the next
        # send must still report it.
        assert check(throttle).suppressed == 1

    def test_failed_send_carries_its_dropped_count_forward(self):
        clock = FakeClock()
        throttle = make_throttle(clock)
        for i in range(4):
            check(throttle, message=f"user {i}", max_per_minute=2)

        clock.advance(61)
        decision = check(throttle, message="later", max_per_minute=2)
        assert decision.dropped == 2
        throttle.record_failure(decision)

        clock.advance(61)
        assert check(throttle, message="later again", max_per_minute=2).dropped == 2

    def test_ignores_a_decision_that_did_not_send(self):
        throttle = make_throttle()
        check(throttle)
        throttle.record_failure(check(throttle))
        assert check(throttle).send is False


class TestStateGrowth:
    def test_tracked_signatures_stay_bounded(self):
        """A pathological spread of distinct messages must not grow forever."""
        clock = FakeClock()
        throttle = make_throttle(clock)
        sent = 0
        for i in range(5000):
            # Advance past the pacing interval so every check is granted;
            # otherwise this sheds on pacing and never reaches the cap.
            if check(throttle, message=f"user {i}", max_per_minute=10_000).send:
                sent += 1
            clock.advance(MIN_SEND_SPACING_SECONDS)
        assert sent == 5000
        assert len(throttle._windows) <= MAX_TRACKED_SIGNATURES

    def test_expiry_still_runs_once_the_table_is_full(self):
        """At the cap the table used to pin there, never reclaiming anything.

        The expiry pass was skipped once full, so an expired, empty window
        was never collected and every send evicted a live one to make room.
        """
        clock = FakeClock()
        throttle = make_throttle(clock)
        for i in range(MAX_TRACKED_SIGNATURES + 100):
            check(throttle, message=f"user {i}", dedup_window=3600,
                  max_per_minute=10_000)
            clock.advance(MIN_SEND_SPACING_SECONDS)

        # The sweep leaves room for the window each send opens, so the
        # table settles at the cap rather than one below it.
        assert len(throttle._windows) == MAX_TRACKED_SIGNATURES

        # Every window is now long expired and holds nothing to report.
        clock.advance(3700)
        check(throttle, message="fresh", dedup_window=3600, max_per_minute=10_000)

        assert len(throttle._windows) == 1

    def test_a_window_holding_a_count_outlives_clean_ones(self):
        """Evicting the oldest first would drop the biggest counts first.

        The oldest window is the long dedup window most likely to be
        holding suppressed occurrences nobody has been told about yet.
        """
        clock = FakeClock()
        throttle = make_throttle(clock)
        check(throttle, message="important", dedup_window=3600)
        for _ in range(5):
            check(throttle, message="important", dedup_window=3600)
        key = (WEBHOOK, signature("signup", "important"))
        assert throttle._windows[key].suppressed == 5

        # Flood the table with distinct, newer signatures.
        for i in range(MAX_TRACKED_SIGNATURES * 2):
            clock.advance(MIN_SEND_SPACING_SECONDS)
            check(throttle, message=f"noise {i}", dedup_window=3600,
                  max_per_minute=10_000)

        assert key in throttle._windows
        assert throttle._windows[key].suppressed == 5

    def test_dropping_a_count_is_logged(self, caplog):
        """Losing a suppression note is invisible otherwise."""
        clock = FakeClock()
        throttle = make_throttle(clock)
        # Every window holds a count, so the cap has nothing clean to take.
        for i in range(MAX_TRACKED_SIGNATURES):
            check(throttle, message=f"user {i}", dedup_window=3600,
                  max_per_minute=10_000)
            check(throttle, message=f"user {i}", dedup_window=3600,
                  max_per_minute=10_000)
            clock.advance(MIN_SEND_SPACING_SECONDS)

        with caplog.at_level(logging.WARNING, logger="hibiki_discord"):
            check(throttle, message="one more", dedup_window=3600,
                  max_per_minute=10_000)

        assert "will not be reported" in caplog.text

    def test_send_timestamps_stay_bounded(self):
        clock = FakeClock()
        throttle = make_throttle(clock)
        for i in range(500):
            check(throttle, message=f"user {i}", max_per_minute=10)
            clock.advance(1)
        assert len(throttle._sends[WEBHOOK]) <= 10


class TestPacing:
    """The budget bounds the average; Discord's limit is on the instant."""

    def test_sends_arriving_together_are_spaced(self):
        throttle = make_throttle()
        delays = [
            check(throttle, message=f"user {i}", max_per_minute=30).delay
            for i in range(4)
        ]
        assert delays == [0.0, 0.5, 1.0, 1.5]

    def test_a_caller_that_waits_is_not_paced(self):
        clock = FakeClock()
        throttle = make_throttle(clock)
        for _ in range(4):
            assert check(throttle, message="hello", dedup_window=0).delay == 0.0
            clock.advance(MIN_SEND_SPACING_SECONDS)

    def test_pacing_past_the_cap_sheds_and_counts(self):
        """Held longer than the budget window is worse than dropped."""
        throttle = make_throttle()
        granted = 0
        for i in range(400):
            if check(throttle, message=f"user {i}", max_per_minute=10_000).send:
                granted += 1

        assert granted == int(MAX_PACING_DELAY_SECONDS / MIN_SEND_SPACING_SECONDS) + 1
        # The shed notifications are reported on the next send that lands.
        assert throttle._dropped_pending[WEBHOOK] == 400 - granted


class TestRetryAccounting:
    def test_retries_consume_budget(self):
        """One notification retried four times costs four slots, not one."""
        throttle = make_throttle()
        first = check(throttle, message="user 1", max_per_minute=4)
        assert first.send is True
        throttle.record_retries(WEBHOOK, 3)

        assert check(throttle, message="user 2", max_per_minute=4).send is False

    def test_a_clean_send_costs_one_slot(self):
        throttle = make_throttle()
        for i in range(4):
            assert check(throttle, message=f"user {i}", max_per_minute=4).send is True
            throttle.record_retries(WEBHOOK, 0)
        assert check(throttle, message="fifth", max_per_minute=4).send is False

    def test_charged_retries_expire_with_the_window(self):
        clock = FakeClock()
        throttle = make_throttle(clock)
        check(throttle, message="user 1", max_per_minute=4)
        throttle.record_retries(WEBHOOK, 3)

        clock.advance(61)
        assert check(throttle, message="user 2", max_per_minute=4).send is True

    def test_retries_on_one_webhook_do_not_shed_another(self):
        throttle = make_throttle()
        check(throttle, message="user 1", max_per_minute=4)
        throttle.record_retries(WEBHOOK, 3)

        assert check(
            throttle, message="user 2", webhook=OTHER_WEBHOOK, max_per_minute=4
        ).send is True


class TestReset:
    def test_reset_clears_state(self):
        throttle = make_throttle()
        check(throttle)
        throttle.reset()
        assert check(throttle).send is True


class TestMixedWindows:
    def test_one_types_window_does_not_expire_anothers(self):
        """Each signature expires on its own window, not the caller's.

        Mixing an incident type at 300 seconds with ordinary types on the
        default 0 is the normal configuration, and a send from the latter
        must not clear the former's dedup state.
        """
        clock = FakeClock()
        throttle = make_throttle(clock)

        assert check(throttle, ntype="incident", message="down",
                     dedup_window=300).send is True

        clock.advance(10)
        check(throttle, ntype="signup", message="new user",
              webhook=OTHER_WEBHOOK, dedup_window=0)

        clock.advance(10)
        assert check(throttle, ntype="incident", message="down",
                     dedup_window=300).send is False
