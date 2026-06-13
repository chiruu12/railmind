"""strip_markdown — agent text must reach the feed, passenger app and TTS clean."""

from __future__ import annotations

from app.agents.base import strip_markdown


def test_strips_bold_announcement():
    assert (
        strip_markdown("**Passenger Announcement — Train 12302** Please remain seated.")
        == "Passenger Announcement — Train 12302 Please remain seated."
    )


def test_preserves_snake_case_identifiers():
    s = "delay_min is 5 and train_id stays stable"
    assert strip_markdown(s) == s


def test_strips_headings_lists_and_code():
    assert strip_markdown("# Title\n- one\n2) two\n`code`") == "Title\none\ntwo\ncode"


def test_strips_italic_both_forms():
    assert strip_markdown("*Train 12302* is late") == "Train 12302 is late"
    assert strip_markdown("_Train 12302_ is late") == "Train 12302 is late"


def test_plain_text_is_unchanged():
    s = "Train 12302 is running 25 min late (loco traction failure)."
    assert strip_markdown(s) == s
