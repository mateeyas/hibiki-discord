import asyncio

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

import hibiki_discord.throttle as throttle_module
from hibiki_discord.config import load_config_from_dict, reset
from hibiki_discord.service import send, send_notification, anonymize_email, fire_notification
from hibiki_discord.throttle import DiscordThrottle


@pytest.fixture(autouse=True)
def clean_config():
    reset()
    yield
    reset()


class TestAnonymizeEmail:
    def test_simple_email(self):
        assert anonymize_email("user@domain.com") == "u***@domain.com"

    def test_dotted_email(self):
        assert anonymize_email("john.doe@example.com") == "j***@example.com"

    def test_single_char_parts(self):
        assert anonymize_email("a.b@test.com") == "a***@test.com"

    def test_empty_string(self):
        assert anonymize_email("") == ""

    def test_no_at_sign(self):
        assert anonymize_email("not-an-email") == "not-an-email"

    def test_none_input(self):
        assert anonymize_email(None) is None

    def test_triple_dotted(self):
        result = anonymize_email("first.middle.last@co.uk")
        assert result == "f***@co.uk"

    def test_single_char_local(self):
        assert anonymize_email("a@b.com") == "a***@b.com"


class TestSend:
    @pytest.mark.asyncio
    async def test_returns_false_for_empty_url(self):
        result = await send(webhook_url="", message="test")
        assert result is False

    @pytest.mark.asyncio
    async def test_successful_send(self):
        mock_response = MagicMock()
        mock_response.status = 204

        mock_post_cm = AsyncMock()
        mock_post_cm.__aenter__.return_value = mock_response

        mock_session = MagicMock()
        mock_session.post.return_value = mock_post_cm

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_session)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_client):
            result = await send(
                webhook_url="https://discord.com/api/webhooks/test",
                message="hello",
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_failed_send(self):
        mock_response = MagicMock()
        mock_response.status = 400

        mock_post_cm = AsyncMock()
        mock_post_cm.__aenter__.return_value = mock_response

        mock_session = MagicMock()
        mock_session.post.return_value = mock_post_cm

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_session)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_client):
            result = await send(
                webhook_url="https://discord.com/api/webhooks/test",
                message="hello",
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_includes_username_in_payload(self):
        mock_response = MagicMock()
        mock_response.status = 204

        mock_post_cm = AsyncMock()
        mock_post_cm.__aenter__.return_value = mock_response

        mock_session = MagicMock()
        mock_session.post.return_value = mock_post_cm

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_session)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_client):
            await send(
                webhook_url="https://discord.com/api/webhooks/test",
                message="hello",
                username="Test Bot",
            )
            call_kwargs = mock_session.post.call_args[1]
            assert call_kwargs["json"]["username"] == "Test Bot"


class TestSendNotification:
    @pytest.mark.asyncio
    async def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown notification type"):
            await send_notification("nonexistent")

    @pytest.mark.asyncio
    async def test_disabled_notification_returns_false(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK", "https://discord.com/api/webhooks/test")
        load_config_from_dict({
            "test": {
                "webhook_url_env": "WEBHOOK",
                "message_template": "Hi",
                "enabled": False,
            }
        })
        result = await send_notification("test")
        assert result is False

    @pytest.mark.asyncio
    async def test_missing_env_var_returns_false(self, monkeypatch):
        monkeypatch.delenv("MISSING_WEBHOOK", raising=False)
        load_config_from_dict({
            "test": {
                "webhook_url_env": "MISSING_WEBHOOK",
                "message_template": "Hi",
            }
        })
        result = await send_notification("test")
        assert result is False

    @pytest.mark.asyncio
    async def test_no_template_raises(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK", "https://discord.com/api/webhooks/test")
        load_config_from_dict({
            "test": {
                "webhook_url_env": "WEBHOOK",
            }
        })
        with pytest.raises(ValueError, match="no message_template"):
            await send_notification("test")

    @pytest.mark.asyncio
    async def test_successful_notification(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK", "https://discord.com/api/webhooks/test")
        load_config_from_dict({
            "signup": {
                "webhook_url_env": "WEBHOOK",
                "username": "Signup Bot",
                "message_template": "New user: {email}",
            }
        })

        with patch(
            "hibiki_discord.service.send",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_send:
            result = await send_notification("signup", email="user@example.com")
            assert result is True
            call_kwargs = mock_send.call_args[1]
            assert call_kwargs["username"] == "Signup Bot"
            assert "u***@example.com" in call_kwargs["message"]

    @pytest.mark.asyncio
    async def test_template_variable_missing_raises(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK", "https://discord.com/api/webhooks/test")
        load_config_from_dict({
            "test": {
                "webhook_url_env": "WEBHOOK",
                "message_template": "Hello {name}",
            }
        })
        with pytest.raises(ValueError, match="Missing template variable"):
            await send_notification("test")

    @pytest.mark.asyncio
    async def test_email_anonymization_in_vars(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK", "https://discord.com/api/webhooks/test")
        load_config_from_dict({
            "test": {
                "webhook_url_env": "WEBHOOK",
                "message_template": "User: {user_email}",
            }
        })

        with patch(
            "hibiki_discord.service.send",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_send:
            await send_notification("test", user_email="john@example.com")
            sent_message = mock_send.call_args[1]["message"]
            assert "j***@example.com" in sent_message


class TestFireNotification:
    @pytest.mark.asyncio
    async def test_returns_task_and_sends(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK", "https://discord.com/api/webhooks/test")
        load_config_from_dict({
            "signup": {
                "webhook_url_env": "WEBHOOK",
                "username": "Signup Bot",
                "message_template": "New user: {email}",
            }
        })

        with patch(
            "hibiki_discord.service.send",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_send:
            import asyncio
            task = fire_notification("signup", email="user@example.com")
            assert isinstance(task, asyncio.Task)
            result = await task
            assert result is True
            mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_logs_errors_instead_of_raising(self):
        import asyncio
        task = fire_notification("nonexistent")
        result = await task
        assert result is False

    @pytest.mark.asyncio
    async def test_does_not_block_caller(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK", "https://discord.com/api/webhooks/test")
        load_config_from_dict({
            "slow": {
                "webhook_url_env": "WEBHOOK",
                "message_template": "Hello",
            }
        })

        import asyncio

        async def slow_send(*args, **kwargs):
            await asyncio.sleep(5)
            return True

        with patch("hibiki_discord.service.send", side_effect=slow_send):
            task = fire_notification("slow")
            assert not task.done()
            task.cancel()


# --- Helpers for the throttling, retry, and embed tests ---------------------


class FakeClock:
    """Monotonic clock under test control."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def install_clock():
    """Replace the process-wide throttle with one on a controllable clock."""
    clock = FakeClock()
    throttle_module._throttle = DiscordThrottle(clock=clock)
    return clock


class FakeResponse:
    def __init__(self, status, headers=None, body=None):
        self.status = status
        self.headers = headers or {}
        self._body = body

    async def json(self, content_type=None):
        if self._body is None:
            raise ValueError("no json body")
        return self._body


def webhook_mock(statuses, headers=None, body=None):
    """Patch aiohttp so each POST returns the next status in turn.

    The final status repeats once the list is exhausted, so a retry test
    only has to name the statuses it cares about.
    """
    responses = [FakeResponse(s, headers=headers, body=body) for s in statuses]
    session = MagicMock()
    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs)
        response = responses[min(len(calls) - 1, len(responses) - 1)]
        cm = AsyncMock()
        cm.__aenter__.return_value = response
        return cm

    session.post.side_effect = post

    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=session)
    client.__aexit__ = AsyncMock(return_value=False)

    return patch("aiohttp.ClientSession", return_value=client), session


def configure(name="signup", **overrides):
    cfg = {
        "webhook_url_env": "WEBHOOK",
        "username": "Signup Bot",
        "message_template": "New user: {email}",
    }
    cfg.update(overrides)
    load_config_from_dict({name: cfg})


class TestSendRetries:
    @pytest.mark.asyncio
    async def test_retries_after_429_and_succeeds(self):
        """A rate limit must delay the send, not lose it."""
        patcher, session = webhook_mock([429, 204], headers={"Retry-After": "0.5"})
        with patcher, patch(
            "hibiki_discord.service.asyncio.sleep", new_callable=AsyncMock
        ) as sleep:
            result = await send(webhook_url="https://wh", message="hi")

        assert result is True
        assert session.post.call_count == 2
        assert sleep.await_args[0][0] == 0.5

    @pytest.mark.asyncio
    async def test_retry_after_body_beats_header(self):
        patcher, _ = webhook_mock(
            [429, 204], headers={"Retry-After": "9"}, body={"retry_after": 1.5}
        )
        with patcher, patch(
            "hibiki_discord.service.asyncio.sleep", new_callable=AsyncMock
        ) as sleep:
            await send(webhook_url="https://wh", message="hi")

        assert sleep.await_args[0][0] == 1.5

    @pytest.mark.asyncio
    async def test_gives_up_when_discord_asks_for_longer_than_the_cap(self):
        """Retrying before the limit clears would only extend it."""
        patcher, session = webhook_mock([429], headers={"Retry-After": "120"})
        with patcher, patch(
            "hibiki_discord.service.asyncio.sleep", new_callable=AsyncMock
        ) as sleep:
            result = await send(webhook_url="https://wh", message="hi")

        assert result is False
        assert session.post.call_count == 1
        sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_retries_on_server_error(self):
        patcher, session = webhook_mock([503, 204])
        with patcher, patch(
            "hibiki_discord.service.asyncio.sleep", new_callable=AsyncMock
        ):
            result = await send(webhook_url="https://wh", message="hi")

        assert result is True
        assert session.post.call_count == 2

    @pytest.mark.asyncio
    async def test_does_not_retry_a_client_error(self):
        patcher, session = webhook_mock([400])
        with patcher:
            result = await send(webhook_url="https://wh", message="hi")

        assert result is False
        assert session.post.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_are_bounded(self):
        patcher, session = webhook_mock([429])
        with patcher, patch(
            "hibiki_discord.service.asyncio.sleep", new_callable=AsyncMock
        ):
            result = await send(webhook_url="https://wh", message="hi")

        assert result is False
        assert session.post.call_count == 4

    @pytest.mark.asyncio
    async def test_accepts_200_as_success(self):
        patcher, _ = webhook_mock([200])
        with patcher:
            assert await send(webhook_url="https://wh", message="hi") is True


class TestNotificationThrottling:
    @pytest.mark.asyncio
    async def test_burst_of_identical_notifications_is_bounded(self, monkeypatch):
        """On defaults the budget alone keeps a 500 message burst bounded."""
        monkeypatch.setenv("WEBHOOK", "https://discord.com/api/webhooks/test")
        configure(message_template="Payment processor unreachable")

        with patch(
            "hibiki_discord.service.send", new_callable=AsyncMock, return_value=True
        ) as mock_send:
            for _ in range(500):
                await send_notification("signup")

        assert mock_send.call_count == 30

    @pytest.mark.asyncio
    async def test_dedup_collapses_a_burst_when_enabled(self, monkeypatch):
        """With dedup on, 500 identical notifications become one call."""
        monkeypatch.setenv("WEBHOOK", "https://discord.com/api/webhooks/test")
        configure(message_template="Payment processor unreachable",
                  dedup_window=300)

        with patch(
            "hibiki_discord.service.send", new_callable=AsyncMock, return_value=True
        ) as mock_send:
            for _ in range(500):
                await send_notification("signup")

        assert mock_send.call_count == 1

    @pytest.mark.asyncio
    async def test_dedup_is_off_by_default(self, monkeypatch):
        """Anonymization renders distinct signups identically.

        `alice@x.com` and `amir@x.com` both become `a***@x.com`, so dedup
        on by default would collapse two customers into one message.
        """
        monkeypatch.setenv("WEBHOOK", "https://discord.com/api/webhooks/test")
        configure()

        with patch(
            "hibiki_discord.service.send", new_callable=AsyncMock, return_value=True
        ) as mock_send:
            await send_notification("signup", email="alice@example.com")
            await send_notification("signup", email="amir@example.com")

        assert mock_send.call_count == 2

    @pytest.mark.asyncio
    async def test_burst_of_distinct_messages_is_capped_by_budget(self, monkeypatch):
        """Distinct messages cannot collapse, so the budget backstops them."""
        monkeypatch.setenv("WEBHOOK", "https://discord.com/api/webhooks/test")
        configure(max_per_minute=10)

        with patch(
            "hibiki_discord.service.send", new_callable=AsyncMock, return_value=True
        ) as mock_send:
            for i in range(500):
                await send_notification("signup", email=f"user{i}@example.com")

        assert mock_send.call_count == 10

    @pytest.mark.asyncio
    async def test_counts_surface_in_the_next_message(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK", "https://discord.com/api/webhooks/test")
        configure(message_template="Payment processor unreachable", dedup_window=60)
        clock = install_clock()

        with patch(
            "hibiki_discord.service.send", new_callable=AsyncMock, return_value=True
        ) as mock_send:
            for _ in range(143):
                await send_notification("signup")
            clock.advance(61)
            await send_notification("signup")

        assert mock_send.call_count == 2
        assert (
            "142 further occurrences suppressed."
            in mock_send.call_args[1]["message"]
        )

    @pytest.mark.asyncio
    async def test_quiet_channel_renders_exactly_as_before(self, monkeypatch):
        """No counts to report means no footer, so nothing changes."""
        monkeypatch.setenv("WEBHOOK", "https://discord.com/api/webhooks/test")
        configure()

        with patch(
            "hibiki_discord.service.send", new_callable=AsyncMock, return_value=True
        ) as mock_send:
            await send_notification("signup", email="user@example.com")

        assert mock_send.call_args[1]["message"] == "New user: u***@example.com"

    @pytest.mark.asyncio
    async def test_failed_send_does_not_suppress_the_next(self, monkeypatch):
        """A webhook outage must not silence the notification behind it."""
        monkeypatch.setenv("WEBHOOK", "https://discord.com/api/webhooks/test")
        configure(message_template="Payment processor unreachable",
                  dedup_window=300)

        with patch(
            "hibiki_discord.service.send", new_callable=AsyncMock, return_value=False
        ) as mock_send:
            await send_notification("signup")
            await send_notification("signup")

        assert mock_send.call_count == 2

    @pytest.mark.asyncio
    async def test_env_var_configures_the_dedup_window(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK", "https://discord.com/api/webhooks/test")
        monkeypatch.setenv("HIBIKI_DISCORD_DEDUP_WINDOW", "300")
        configure(message_template="Order placed")

        with patch(
            "hibiki_discord.service.send", new_callable=AsyncMock, return_value=True
        ) as mock_send:
            for _ in range(3):
                await send_notification("signup")

        assert mock_send.call_count == 1

    @pytest.mark.asyncio
    async def test_per_type_window_overrides_the_env_var(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK", "https://discord.com/api/webhooks/test")
        monkeypatch.setenv("HIBIKI_DISCORD_DEDUP_WINDOW", "300")
        configure(message_template="Order placed", dedup_window=0)

        with patch(
            "hibiki_discord.service.send", new_callable=AsyncMock, return_value=True
        ) as mock_send:
            for _ in range(3):
                await send_notification("signup")

        assert mock_send.call_count == 3

    @pytest.mark.asyncio
    async def test_budget_is_per_webhook(self, monkeypatch):
        """One noisy notification type must not shed another's messages."""
        monkeypatch.setenv("WEBHOOK_A", "https://discord.com/api/webhooks/a")
        monkeypatch.setenv("WEBHOOK_B", "https://discord.com/api/webhooks/b")
        load_config_from_dict({
            "noisy": {
                "webhook_url_env": "WEBHOOK_A",
                "message_template": "Event {n}",
                "max_per_minute": 2,
            },
            "quiet": {
                "webhook_url_env": "WEBHOOK_B",
                "message_template": "Signup {n}",
                "max_per_minute": 2,
            },
        })

        with patch(
            "hibiki_discord.service.send", new_callable=AsyncMock, return_value=True
        ):
            for i in range(10):
                await send_notification("noisy", n=i)
            assert await send_notification("quiet", n=1) is True


class TestNotificationEmbeds:
    @pytest.mark.asyncio
    async def test_plain_text_is_the_default(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK", "https://discord.com/api/webhooks/test")
        configure()
        patcher, session = webhook_mock([204])

        with patcher:
            await send_notification("signup", email="user@example.com")

        payload = session.post.call_args[1]["json"]
        assert payload["content"] == "New user: u***@example.com"
        assert "embeds" not in payload

    @pytest.mark.asyncio
    async def test_embed_true_sends_an_embed(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK", "https://discord.com/api/webhooks/test")
        configure(embed=True, embed_title="Signup", embed_color="#5865F2")
        patcher, session = webhook_mock([204])

        with patcher:
            await send_notification("signup", email="user@example.com")

        payload = session.post.call_args[1]["json"]
        embed = payload["embeds"][0]
        assert embed["description"] == "New user: u***@example.com"
        assert embed["title"] == "Signup"
        assert embed["color"] == 0x5865F2
        assert "content" not in payload

    @pytest.mark.asyncio
    async def test_counts_go_in_the_embed_footer(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK", "https://discord.com/api/webhooks/test")
        configure(message_template="Processor unreachable", embed=True,
                  dedup_window=60)
        clock = install_clock()
        patcher, session = webhook_mock([204])

        with patcher:
            await send_notification("signup")
            await send_notification("signup")
            clock.advance(61)
            await send_notification("signup")

        embed = session.post.call_args[1]["json"]["embeds"][0]
        assert embed["footer"]["text"] == "1 further occurrence suppressed."

    @pytest.mark.asyncio
    async def test_oversized_message_stays_within_discord_limits(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK", "https://discord.com/api/webhooks/test")
        configure(message_template="{blob}", embed=True)
        patcher, session = webhook_mock([204])

        with patcher:
            await send_notification("signup", blob="x" * 20_000)

        embed = session.post.call_args[1]["json"]["embeds"][0]
        assert len(embed["description"]) <= 4096

    @pytest.mark.asyncio
    async def test_falls_back_to_plain_text_when_the_embed_fails(self, monkeypatch):
        """A notification that looks wrong beats no notification."""
        monkeypatch.setenv("WEBHOOK", "https://discord.com/api/webhooks/test")
        configure(embed=True)
        patcher, session = webhook_mock([204])

        with patcher, patch(
            "hibiki_discord.service.build_notification_embed",
            side_effect=RuntimeError("boom"),
        ):
            result = await send_notification("signup", email="user@example.com")

        assert result is True
        payload = session.post.call_args[1]["json"]
        assert payload["content"] == "New user: u***@example.com"
        assert "embeds" not in payload


class TestThrottleRollback:
    @pytest.mark.asyncio
    async def test_cancelled_send_does_not_suppress_the_next(self, monkeypatch):
        """A send cancelled mid-flight reached Discord no more than a failed one."""
        monkeypatch.setenv("WEBHOOK", "https://discord.com/api/webhooks/test")
        configure(message_template="Processor unreachable", dedup_window=300)

        async def cancelled_send(*args, **kwargs):
            raise asyncio.CancelledError()

        with patch("hibiki_discord.service.send", side_effect=cancelled_send):
            with pytest.raises(asyncio.CancelledError):
                await send_notification("signup")

        with patch(
            "hibiki_discord.service.send", new_callable=AsyncMock, return_value=True
        ) as mock_send:
            assert await send_notification("signup") is True
        assert mock_send.call_count == 1

    @pytest.mark.asyncio
    async def test_occurrences_during_a_failed_send_are_still_counted(
        self, monkeypatch
    ):
        """Suppressions collected while a send was in flight must survive."""
        monkeypatch.setenv("WEBHOOK", "https://discord.com/api/webhooks/test")
        configure(message_template="Processor unreachable", dedup_window=300)

        release = asyncio.Event()

        async def blocking_send(*args, **kwargs):
            await release.wait()
            return False

        with patch("hibiki_discord.service.send", side_effect=blocking_send):
            first = asyncio.create_task(send_notification("signup"))
            await asyncio.sleep(0)
            # These collapse into the send that is still in flight.
            for _ in range(9):
                await send_notification("signup")
            release.set()
            assert await first is False

        with patch(
            "hibiki_discord.service.send", new_callable=AsyncMock, return_value=True
        ) as mock_send:
            await send_notification("signup")

        assert "9 further occurrences suppressed." in mock_send.call_args[1]["message"]

    @pytest.mark.asyncio
    async def test_footer_survives_an_oversized_message(self, monkeypatch):
        """The count must not be the part truncation eats."""
        monkeypatch.setenv("WEBHOOK", "https://discord.com/api/webhooks/test")
        configure(message_template="{blob}", dedup_window=300)
        clock = install_clock()
        patcher, session = webhook_mock([204])

        with patcher:
            for _ in range(3):
                await send_notification("signup", blob="x" * 2500)
            clock.advance(301)
            await send_notification("signup", blob="x" * 2500)

        content = session.post.call_args[1]["json"]["content"]
        assert len(content) <= 2000
        assert content.endswith("_2 further occurrences suppressed._")
