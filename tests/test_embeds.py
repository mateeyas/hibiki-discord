import json

import pytest

from hibiki_discord.embeds import (
    DEFAULT_COLOR,
    DESCRIPTION_LIMIT,
    TITLE_LIMIT,
    TOTAL_LIMIT,
    build_footer,
    build_notification_embed,
    parse_color,
    truncate_end,
)


def embed_length(embed):
    """Total character count Discord charges the 6000 limit against."""
    return (
        len(embed.get("title") or "")
        + len(embed.get("description") or "")
        + len((embed.get("footer") or {}).get("text") or "")
    )


class TestParseColor:
    def test_none_stays_none(self):
        assert parse_color(None) is None

    def test_integer_passes_through(self):
        assert parse_color(0x5865F2) == 0x5865F2

    def test_hex_string_with_hash(self):
        assert parse_color("#5865F2") == 0x5865F2

    def test_hex_string_without_hash(self):
        assert parse_color("5865f2") == 0x5865F2

    def test_rejects_nonsense(self):
        with pytest.raises(ValueError):
            parse_color("not-a-colour")

    def test_rejects_out_of_range(self):
        with pytest.raises(ValueError):
            parse_color(0x1000000)

    def test_rejects_bool(self):
        with pytest.raises(ValueError):
            parse_color(True)


class TestTruncateEnd:
    def test_short_text_untouched(self):
        assert truncate_end("hello", 100) == "hello"

    def test_marks_the_cut(self):
        result = truncate_end("x" * 100, 20)
        assert len(result) == 20
        assert result.endswith("...")

    def test_zero_limit(self):
        assert truncate_end("hello", 0) == ""


class TestBuildFooter:
    def test_nothing_to_report(self):
        assert build_footer(0, 0) is None

    def test_suppressed_plural(self):
        assert build_footer(142, 0) == "142 further occurrences suppressed."

    def test_suppressed_singular(self):
        assert build_footer(1, 0) == "1 further occurrence suppressed."

    def test_dropped(self):
        assert build_footer(0, 3) == "3 notifications dropped by the send budget."

    def test_both(self):
        footer = build_footer(2, 3)
        assert "2 further occurrences suppressed." in footer
        assert "3 notifications dropped by the send budget." in footer


class TestBuildNotificationEmbed:
    def test_minimal_embed(self):
        embed = build_notification_embed(message="New user signed up")
        assert embed["description"] == "New user signed up"
        assert embed["color"] == DEFAULT_COLOR
        assert "timestamp" in embed
        assert "title" not in embed
        assert "footer" not in embed

    def test_title_and_color(self):
        embed = build_notification_embed(
            message="hi", title="Signup", color=0xFF0000
        )
        assert embed["title"] == "Signup"
        assert embed["color"] == 0xFF0000

    def test_counts_go_in_the_footer_not_the_body(self):
        embed = build_notification_embed(
            message="hi", suppressed_count=142, dropped_count=3
        )
        assert "142" in embed["footer"]["text"]
        assert "142" not in embed["description"]

    def test_oversized_message_stays_within_limits(self):
        embed = build_notification_embed(
            message="x" * 20_000,
            title="y" * 500,
            suppressed_count=99,
        )
        assert len(embed["description"]) <= DESCRIPTION_LIMIT
        assert len(embed["title"]) <= TITLE_LIMIT
        assert embed_length(embed) <= TOTAL_LIMIT

    def test_result_is_json_serialisable(self):
        embed = build_notification_embed(message="hi", title="Signup")
        assert json.loads(json.dumps(embed))["description"] == "hi"

    def test_empty_message_does_not_raise(self):
        assert build_notification_embed(message="")["description"] == ""
