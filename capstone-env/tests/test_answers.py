"""Written answer vs computed exe_ans (decision A)."""

from __future__ import annotations

import pytest

from capstone.data.answers import KEEP, classify, parse_written


@pytest.mark.parametrize("written,gold,expected", [
    # agree within the team tolerance
    ("17.4%", 0.174, "agree"),
    ("1.7%", 0.01743, "agree"),            # small gold: absolute 0.001
    ("$ 3.8 million", 3.8, "agree"),
    ("1,234", 1234.0, "agree"),
    ("-.62", -0.62, "agree"),              # leading-dot number
    ("yes", "yes", "agree"),
    # display rounding: within half a unit of the last digit shown
    ("1.7", 1.7428, "agree_display_rounding"),
    ("17%", 0.1743, "agree_display_rounding"),
    # '%' written but exe_ans already in percent points
    ("17.4%", 17.4, "agree_percent_format"),
    ("17%", 17.4, "agree_percent_format"),
    # disagreements
    ("380", 3.8, "disagree"),              # FinQA train[0]
    ("-50.1%", 0.50086, "disagree"),       # sign flip
    ("-0.1%", 0.001, "disagree"),          # sign flip even though tiny
    ("-32%", -0.3282, "disagree"),         # truncated, not rounded
    ("28%", 3.6e13, "disagree"),
    ("no", "yes", "disagree"),
    ("yes", 1.0, "disagree"),
    # nothing to compare
    ("n/a", 1.0, "unparseable"),
    ("", 1.0, "unparseable"),
])
def test_classify(written, gold, expected):
    assert classify(written, gold) == expected


def test_keep_is_formatting_only():
    assert KEEP == {"agree", "agree_display_rounding", "agree_percent_format"}


def test_parse_written_records_decimals_and_percent():
    w = parse_written("14.30%")
    assert (w.kind, w.number, w.is_percent, w.decimals) == ("number", 14.3, True, 2)
    assert parse_written("Yes").kind == "yesno"
    assert parse_written("about").kind == "none"
