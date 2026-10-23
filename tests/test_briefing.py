"""Tests for daily_briefing, the morning-routine tool."""

from datetime import date
from unittest.mock import MagicMock


def s():
    import server
    return server


def make_ctx(user_id):
    ctx = MagicMock()
    ctx.request_context.request.headers = {"x-user-id": user_id}
    return ctx


def test_all_clear():
    s().add_item("rice", unit="kg", days_until_expiry=30)
    assert s().daily_briefing() == (
        "Good news, nothing in your pantry or medicine cabinet needs attention today."
    )


def test_full_briefing():
    srv = s()
    srv.add_item("spinach", unit="bag", days_until_expiry=1)
    srv.add_item("eggs", quantity=6, days_until_expiry=10)        # not mentioned: > tomorrow
    srv.add_medicine("cough syrup", form="bottle", expiry="2025-01")
    srv.add_medicine("cetirizine", days_until_expiry=4)
    srv.add_medicine("vitamin d", expiry="2028-01")                # not mentioned: > 1 week
    srv.schedule_meal("pasta carbonara", meal_date=date.today().isoformat())
    srv.add_to_shopping_list("milk")
    out = srv.daily_briefing()
    assert out.startswith("Spinach expires tomorrow, so use it first.")
    assert "cough syrup has expired, so please set it aside to dispose of safely" in out
    assert "Your cetirizine expires on" in out
    assert "Tonight's plan is pasta carbonara." in out
    assert out.endswith("You have 1 thing on your shopping list.")
    assert "eggs" not in out and "vitamin" not in out


def test_suggests_cooking_when_food_expires_and_no_meal_planned():
    s().add_item("spinach", days_until_expiry=1)
    assert "Ask me what to cook tonight" in s().daily_briefing()


def test_expired_food_reported():
    srv = s()
    srv.add_item("milk", days_until_expiry=1)
    import sqlite3
    con = sqlite3.connect(srv.DB)
    con.execute("UPDATE items SET expires='2020-01-01' WHERE name='milk'")
    con.commit()
    assert s().daily_briefing().startswith("Your milk has already expired.")


def test_owner_isolation():
    srv = s()
    srv.add_item("spinach", days_until_expiry=1, ctx=make_ctx("alice"))
    assert "Good news" in srv.daily_briefing(ctx=make_ctx("bob"))
    assert "spinach" in srv.daily_briefing(ctx=make_ctx("alice")).lower()


def test_voice_first_and_short():
    srv = s()
    srv.add_item("spinach", days_until_expiry=0)
    srv.add_medicine("old syrup", expiry="2025-01")
    out = srv.daily_briefing()
    for ch in ("#", "*", "`", "|", "\n"):
        assert ch not in out
    assert len(out.split(". ")) <= 6
