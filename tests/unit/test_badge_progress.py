"""Tests for the pure badge progress logic (parsing, classification, estimates)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "builder_publish"))

from badge_progress import (  # noqa: E402
    BadgeProgress,
    classify,
    diff_snapshots,
    estimate_finish,
    parse_progress_text,
    priorities,
    progress_bar,
    render_dashboard,
    target_from_description,
)

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TODAY = date(2026, 11, 19)

# The 21 badges, with the descriptions Builder Center shows.
_EARNED = {
    "7-Day Visit Streak": "Visit Builder Center once a day for 7 consecutive days while signed in.",
    "7-Day Like Streak": "Leave at least one like on any content each day for seven consecutive days.",
    "4-Week Article Publishing Streak": "Publish an article weekly for four consecutive weeks.",
    "Valued Creator": "Get 10 likes on five of your articles.",
    "First Article": "Publish your first article.",
    "First Wish": "Publish your first wish in AWS Wishlist.",
    "Discussion Debut": "Join the conversation by leaving one comment on any content.",
    "Hello, World!": "Complete the About section in your profile to introduce yourself in Builder Center.",
    "Knowledge Seeker": "Read 10 different articles.",
    "Photo Finisher": "Upload a photo to your profile.",
}
_OPEN = {
    "30-Day Visit Streak": "Visit Builder Center at least once a day for 30 consecutive days while signed in.",
    "90-Day Visit Streak": "Visit Builder Center at least once a day for 90 consecutive days while signed in.",
    "30-Day Like Streak": "Leave at least one like on any content each day for 30 consecutive days.",
    "90-Day Like Streak": "Leave at least one like on any content each day for 90 consecutive days.",
    "7-Day Comment Streak": "Leave at least one comment on any content each day for seven consecutive days.",
    "30-Day Comment Streak": "Leave at least one comment on any content each day for 30 consecutive days.",
    "90-Day Comment Streak": "Leave at least one comment on any content each day for 90 consecutive days.",
    "Conversation Starter": "Get a reply on 10 of your comments.",
    "Meaningful Contributor": "Get 10 likes on five of your comments.",
    "Idea Influencer": "Get 10 votes on your wishes.",
    "4-Week Wish Vote Streak": "Vote on a wish each week for four consecutive weeks.",
}


def _all_badges(visit_current: int = 18) -> list[BadgeProgress]:
    badges = [BadgeProgress(t, d, "earned") for t, d in _EARNED.items()]
    for title, description in _OPEN.items():
        if title == "30-Day Visit Streak":
            badges.append(BadgeProgress(
                title, description, "in_progress", visit_current, 30, "days", None,
            ))
        else:
            badges.append(BadgeProgress(title, description, "not_started"))
    return badges


# -- parsing -------------------------------------------------------------------


def test_parses_the_real_popup_text() -> None:
    text = (FIXTURES / "badge_popup_synthetic.txt").read_text(encoding="utf-8")
    assert parse_progress_text(text) == (18, 30, "days", 60)


def test_parses_weeks_and_derives_missing_percent() -> None:
    assert parse_progress_text("2 / 4 weeks") == (2, 4, "weeks", 50)


def test_missing_progress_is_none() -> None:
    assert parse_progress_text("Get 10 votes on your wishes.") == (None, None, "", None)


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("for seven consecutive days.", (7, "days")),
        ("for 30 consecutive days while signed in.", (30, "days")),
        ("weekly for four consecutive weeks.", (4, "weeks")),
        ("Publish your first article.", (None, "")),
    ],
)
def test_target_from_description(description: str, expected: tuple[int | None, str]) -> None:
    assert target_from_description(description) == expected


# -- classification --------------------------------------------------------------


def test_every_badge_is_classified_as_expected() -> None:
    kinds = {b.title: classify(b) for b in _all_badges()}
    assert kinds["7-Day Visit Streak"].action == "visit"
    assert kinds["30-Day Like Streak"].action == "like"
    assert kinds["90-Day Comment Streak"].action == "comment"
    assert (kinds["4-Week Article Publishing Streak"].action, kinds["4-Week Article Publishing Streak"].period) == ("publish", "weekly")
    assert (kinds["4-Week Wish Vote Streak"].action, kinds["4-Week Wish Vote Streak"].period) == ("vote", "weekly")
    for impact in ("Valued Creator", "Conversation Starter", "Meaningful Contributor", "Idea Influencer"):
        assert kinds[impact].kind == "impact"
    for oneoff in ("First Article", "First Wish", "Hello, World!", "Photo Finisher", "Knowledge Seeker", "Discussion Debut"):
        assert kinds[oneoff].kind == "oneoff"


# -- estimates -------------------------------------------------------------------


def test_visit_streak_finish_is_twelve_days_out() -> None:
    badge = next(b for b in _all_badges() if b.title == "30-Day Visit Streak")
    assert estimate_finish(badge, TODAY) == date(2026, 12, 1)


def test_not_started_streak_counts_from_zero() -> None:
    badge = next(b for b in _all_badges() if b.title == "7-Day Comment Streak")
    assert estimate_finish(badge, TODAY) == date(2026, 11, 26)


def test_weekly_streak_counts_in_weeks() -> None:
    badge = BadgeProgress(
        "4-Week Wish Vote Streak", _OPEN["4-Week Wish Vote Streak"], "in_progress", 1, 4, "weeks", 25,
    )
    assert estimate_finish(badge, TODAY) == date(2026, 12, 10)


def test_impact_and_earned_badges_get_no_date() -> None:
    by_title = {b.title: b for b in _all_badges()}
    assert estimate_finish(by_title["Conversation Starter"], TODAY) is None
    assert estimate_finish(by_title["Valued Creator"], TODAY) is None


# -- snapshot diff ---------------------------------------------------------------


def test_diff_reports_progress_earned_reset_and_new() -> None:
    old = [
        BadgeProgress("A", "", "in_progress", 5, 30, "days", 16),
        BadgeProgress("B", "", "in_progress", 6, 7, "days", 85),
        BadgeProgress("C", "Visit Builder Center for 7 consecutive days", "in_progress", 9, 30, "days", 30),
    ]
    new = [
        BadgeProgress("A", "", "in_progress", 6, 30, "days", 20),
        BadgeProgress("B", "", "earned"),
        BadgeProgress("C", "Visit Builder Center for 7 consecutive days", "in_progress", 1, 30, "days", 3),
        BadgeProgress("D", "", "not_started"),
    ]
    kinds = {c.title: c.kind for c in diff_snapshots(old, new)}
    assert kinds == {"A": "progress", "B": "earned", "C": "reset", "D": "new"}


def test_no_changes_is_empty() -> None:
    assert diff_snapshots(_all_badges(), _all_badges()) == []


# -- priorities and dashboard ----------------------------------------------------


def test_comment_streak_is_the_first_priority_while_it_is_the_gap() -> None:
    first = priorities(_all_badges())[0]
    assert first.title == "7-Day Comment Streak"
    assert "every day" in first.reason


def test_progress_bar() -> None:
    assert progress_bar(60) == "######---- 60%"
    assert progress_bar(None) == "---------- n/a"
    assert progress_bar(250).startswith("##########")


def test_dashboard_contents_and_style() -> None:
    changes = diff_snapshots(_all_badges(visit_current=17), _all_badges())
    text = render_dashboard(_all_badges(), changes, TODAY)
    assert "10 earned, 11 to go" in text
    assert "30-Day Visit Streak" in text and "18/30 days" in text
    assert "2026-12-01" in text
    assert "depends on others" in text  # impact badges
    assert "17/30 days -> 18/30 days" in text
    assert "\u2014" not in text  # no em dashes
