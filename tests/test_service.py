import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from hibiki_discord.config import load_config_from_dict, reset
from hibiki_discord.service import send, send_notification, anonymize_email, fire_notification


@pytest.fixture(autouse=True)
def clean_config():
    reset()
    yield
    reset()


class TestAnonymizeEmail:
    def test_simple_email(self):
        assert anonymize_email("user@domain.com") == "u***@domain.com"

    def test_dotted_email(self):
        assert anonymize_email("john.doe@example.com") == "j***.d***@example.com"

    def test_single_char_parts(self):
        assert anonymize_email("a.b@test.com") == "a.b@test.com"

    def test_empty_string(self):
        assert anonymize_email("") == ""

    def test_no_at_sign(self):
        assert anonymize_email("not-an-email") == "not-an-email"

    def test_none_input(self):
        assert anonymize_email(None) is None

    def test_triple_dotted(self):
        result = anonymize_email("first.middle.last@co.uk")
        assert result == "f***.m***.l***@co.uk"


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
