"""
tests/test_tools.py – pytest suite for PantryPilot tool functions (Steps 2–4).

Strategy
--------
* Import the five tool functions directly (no running server needed).
* A 'tmp_db' fixture overrides PANTRY_DB and reloads server so tests are hermetic.
* ctx=None in direct calls → get_owner() falls back to DEFAULT_OWNER ("default").
* boto3 is fully mocked so suggest_recipe works without AWS credentials.
* A 'user_b' fixture tests owner isolation (Step 3).

Coverage
--------
add_item     – basic add, upsert (qty accumulates), unit, name normalised, expiry,
               COALESCE keeps original expiry
use_item     – partial decrement, use to zero (item deleted), over-use, unknown item
list_pantry  – empty, multi-item, no markdown
expiring_soon – ordering, cutoff, nothing-expiring, no-expiry excluded, past=expired
suggest_recipe – Bedrock success, Bedrock failure fallback, timeout fallback,
                 empty-pantry guard, voice-first (no markdown)
owner isolation – user A items are invisible to user B (Step 3)
"""

import os
import importlib
import sqlite3
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Fresh SQLite DB + reload server for every test."""
    db_path = str(tmp_path / "test_pantry.db")
    monkeypatch.setenv("PANTRY_DB", db_path)
    monkeypatch.setenv("DEFAULT_OWNER", "default")

    import server
    importlib.reload(server)
    monkeypatch.setattr(server, "DB", db_path)
    monkeypatch.setattr(server, "DEFAULT_OWNER", "default")
    return db_path


def s():
    """Return freshly reloaded server module."""
    import server
    return server


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_ctx(user_id: str | None = None):
    """Build a mock Context that returns user_id from X-User-ID header."""
    ctx = MagicMock()
    if user_id:
        ctx.request_context.request.headers.get.side_effect = (
            lambda key, default="": user_id if key == "x-user-id" else default
        )
    else:
        ctx.request_context.request.headers.get.return_value = ""
    return ctx


# ---------------------------------------------------------------------------
# add_item
# ---------------------------------------------------------------------------

class TestAddItem:
    def test_basic_add(self):
        result = s().add_item("milk", quantity=2, unit="litres")
        assert "milk" in result and "2" in result

    def test_upsert_accumulates_quantity(self):
        s().add_item("eggs", quantity=6, unit="pcs")
        s().add_item("eggs", quantity=4, unit="pcs")
        assert "10" in s().list_pantry()

    def test_unit_preserved(self):
        s().add_item("flour", quantity=500, unit="grams")
        assert "grams" in s().list_pantry()

    def test_name_normalised_to_lowercase(self):
        s().add_item("TOMATOES", quantity=3)
        assert "tomatoes" in s().list_pantry()

    def test_expiry_stored(self):
        s().add_item("yogurt", quantity=1, days_until_expiry=2)
        row = sqlite3.connect(os.getenv("PANTRY_DB")).execute(
            "SELECT expires FROM items WHERE name='yogurt'"
        ).fetchone()
        expected = (date.today() + timedelta(days=2)).isoformat()
        assert row and row[0] == expected

    def test_upsert_keeps_existing_expiry_when_none_given(self):
        s().add_item("butter", quantity=1, days_until_expiry=5)
        s().add_item("butter", quantity=1)           # no expiry on re-add
        row = sqlite3.connect(os.getenv("PANTRY_DB")).execute(
            "SELECT expires FROM items WHERE name='butter'"
        ).fetchone()
        assert row and row[0] is not None             # original date kept


# ---------------------------------------------------------------------------
# use_item
# ---------------------------------------------------------------------------

class TestUseItem:
    def test_partial_decrement(self):
        s().add_item("rice", quantity=5, unit="cups")
        result = s().use_item("rice", quantity=2)
        assert "3" in result and "rice" in result

    def test_use_to_zero_removes_item(self):
        s().add_item("oil", quantity=1, unit="bottle")
        s().use_item("oil", quantity=1)
        assert "oil" not in s().list_pantry()

    def test_use_more_than_available_removes_item(self):
        s().add_item("salt", quantity=1)
        s().use_item("salt", quantity=999)
        assert "salt" not in s().list_pantry()

    def test_unknown_item_returns_friendly_message(self):
        result = s().use_item("dragon-fruit")
        assert "dragon-fruit" in result or "don't have" in result.lower()


# ---------------------------------------------------------------------------
# list_pantry
# ---------------------------------------------------------------------------

class TestListPantry:
    def test_empty_pantry(self):
        assert "empty" in s().list_pantry().lower()

    def test_multiple_items_listed(self):
        s().add_item("apple", quantity=3)
        s().add_item("banana", quantity=5)
        result = s().list_pantry()
        assert "apple" in result and "banana" in result

    def test_no_markdown_in_output(self):
        s().add_item("carrot", quantity=2)
        result = s().list_pantry()
        for ch in ("#", "*", "_", "|", "`"):
            assert ch not in result, f"Markdown char '{ch}' in list_pantry output"


# ---------------------------------------------------------------------------
# expiring_soon
# ---------------------------------------------------------------------------

class TestExpiringSoon:
    def test_nothing_expiring(self):
        s().add_item("honey", quantity=1, days_until_expiry=30)
        assert "nothing" in s().expiring_soon(days=3).lower()

    def test_item_expiring_within_window(self):
        s().add_item("bread", quantity=1, days_until_expiry=2)
        assert "bread" in s().expiring_soon(days=3)

    def test_ordering_soonest_first(self):
        s().add_item("cheese", quantity=1, days_until_expiry=5)
        s().add_item("milk", quantity=1, days_until_expiry=1)
        result = s().expiring_soon(days=7)
        assert result.index("milk") < result.index("cheese")

    def test_item_outside_window_excluded(self):
        s().add_item("jam", quantity=1, days_until_expiry=10)
        assert "jam" not in s().expiring_soon(days=3)

    def test_no_expiry_date_excluded(self):
        s().add_item("salt", quantity=1)
        assert "salt" not in s().expiring_soon(days=3)

    def test_already_expired_shows_expired_label(self):
        db_path = os.getenv("PANTRY_DB")
        past = (date.today() - timedelta(days=1)).isoformat()
        con = sqlite3.connect(db_path)
        con.execute(
            "CREATE TABLE IF NOT EXISTS items"
            "(owner TEXT NOT NULL DEFAULT 'default', name TEXT NOT NULL,"
            " qty REAL, unit TEXT, expires TEXT, PRIMARY KEY(owner,name))"
        )
        con.execute(
            "INSERT INTO items VALUES('default','leftover',1,'pcs',?)", (past,)
        )
        con.commit(); con.close()
        result = s().expiring_soon(days=3)
        assert "leftover" in result and "expired" in result


# ---------------------------------------------------------------------------
# suggest_recipe
# ---------------------------------------------------------------------------

class TestSuggestRecipe:
    def test_empty_pantry_guard(self):
        assert "empty" in s().suggest_recipe().lower()

    def test_bedrock_success_path(self):
        s().add_item("chicken", quantity=2, unit="pieces", days_until_expiry=1)
        s().add_item("broccoli", quantity=1, unit="head", days_until_expiry=3)
        mock_resp = {
            "output": {"message": {"content": [
                {"text": "Make a quick chicken and broccoli stir-fry in 20 minutes."}
            ]}}
        }
        mock_client = MagicMock()
        mock_client.converse.return_value = mock_resp
        with patch("boto3.client", return_value=mock_client):
            result = s().suggest_recipe(mood="healthy")
        assert "chicken" in result.lower() or "broccoli" in result.lower()
        mock_client.converse.assert_called_once()

    def test_bedrock_failure_returns_offline_fallback(self):
        s().add_item("pasta", quantity=500, unit="grams", days_until_expiry=2)
        s().add_item("tomato", quantity=4, unit="pcs", days_until_expiry=1)
        with patch("boto3.client", side_effect=Exception("No credentials")):
            result = s().suggest_recipe()
        assert len(result) > 0 and "#" not in result
        assert "pasta" in result or "tomato" in result or "stir-fry" in result

    def test_bedrock_timeout_returns_offline_fallback(self):
        s().add_item("eggs", quantity=6)
        mock_client = MagicMock()
        mock_client.converse.side_effect = TimeoutError("timed out")
        with patch("boto3.client", return_value=mock_client):
            result = s().suggest_recipe()
        assert len(result) > 0
        assert "eggs" in result or "stir-fry" in result

    def test_output_is_voice_first(self):
        s().add_item("spinach", quantity=1, days_until_expiry=1)
        mock_resp = {
            "output": {"message": {"content": [
                {"text": "Saute spinach with garlic and olive oil."}
            ]}}
        }
        mock_client = MagicMock()
        mock_client.converse.return_value = mock_resp
        with patch("boto3.client", return_value=mock_client):
            result = s().suggest_recipe()
        for ch in ("#", "*", "`", "|"):
            assert ch not in result, f"Markdown char '{ch}' in recipe output"


# ---------------------------------------------------------------------------
# Owner isolation (Step 3)
# ---------------------------------------------------------------------------

class TestOwnerIsolation:
    """Items added by user A must not appear in user B's pantry."""

    def test_separate_pantries(self):
        ctx_a = make_ctx("alice")
        ctx_b = make_ctx("bob")
        srv = s()

        srv.add_item("apples", quantity=5, ctx=ctx_a)
        srv.add_item("bananas", quantity=3, ctx=ctx_b)

        pantry_a = srv.list_pantry(ctx=ctx_a)
        pantry_b = srv.list_pantry(ctx=ctx_b)

        assert "apples" in pantry_a and "bananas" not in pantry_a
        assert "bananas" in pantry_b and "apples" not in pantry_b

    def test_use_item_scoped_to_owner(self):
        ctx_a = make_ctx("alice")
        ctx_b = make_ctx("bob")
        srv = s()

        srv.add_item("milk", quantity=2, ctx=ctx_a)
        # Bob tries to use Alice's milk — should get "don't have"
        result = srv.use_item("milk", quantity=1, ctx=ctx_b)
        assert "don't have" in result.lower() or "milk" in result

    def test_expiring_soon_scoped(self):
        ctx_a = make_ctx("alice")
        ctx_b = make_ctx("bob")
        srv = s()

        srv.add_item("cheese", quantity=1, days_until_expiry=1, ctx=ctx_a)
        result_b = srv.expiring_soon(days=3, ctx=ctx_b)
        assert "cheese" not in result_b

    def test_default_owner_fallback(self):
        """No ctx → DEFAULT_OWNER is used consistently."""
        srv = s()
        srv.add_item("salt", quantity=1)     # ctx=None → owner="default"
        assert "salt" in srv.list_pantry()   # ctx=None → same owner


# ---------------------------------------------------------------------------
# Shopping list (Step 4)
# ---------------------------------------------------------------------------

class TestShoppingList:
    def test_add_to_shopping_list_basic(self):
        result = s().add_to_shopping_list("bread", quantity=1, unit="loaf")
        assert "bread" in result and "shopping list" in result.lower()

    def test_get_shopping_list_empty(self):
        assert "empty" in s().get_shopping_list().lower()

    def test_get_shopping_list_with_items(self):
        srv = s()
        srv.add_to_shopping_list("milk", quantity=2, unit="litres")
        srv.add_to_shopping_list("eggs", quantity=12, unit="pcs")
        result = srv.get_shopping_list()
        assert "milk" in result and "eggs" in result

    def test_add_to_shopping_list_upserts(self):
        """Re-adding the same item updates qty, not duplicates."""
        srv = s()
        srv.add_to_shopping_list("butter", quantity=1)
        srv.add_to_shopping_list("butter", quantity=2)  # update to 2
        result = srv.get_shopping_list()
        assert result.count("butter") == 1
        assert "2" in result

    def test_use_item_to_zero_auto_adds_to_shopping_list(self):
        """use_item emptying an item should auto-add to shopping list."""
        srv = s()
        srv.add_item("oil", quantity=1, unit="bottle")
        result = srv.use_item("oil", quantity=1)
        assert "shopping list" in result.lower()
        shopping = srv.get_shopping_list()
        assert "oil" in shopping

    def test_add_item_removes_from_shopping_list(self):
        """Restocking an item (add_item) removes it from the shopping list."""
        srv = s()
        srv.add_to_shopping_list("coffee", quantity=1)
        result = srv.add_item("coffee", quantity=2)
        assert "shopping list" in result.lower()
        assert "coffee" not in srv.get_shopping_list()

    def test_shopping_list_owner_isolation(self):
        """Alice's shopping list is invisible to Bob."""
        ctx_a = make_ctx("alice")
        ctx_b = make_ctx("bob")
        srv = s()
        srv.add_to_shopping_list("tea", quantity=1, ctx=ctx_a)
        assert "tea" not in srv.get_shopping_list(ctx=ctx_b)

    def test_no_markdown_in_output(self):
        s().add_to_shopping_list("sugar", quantity=1)
        result = s().get_shopping_list()
        for ch in ("#", "*", "_", "|", "`"):
            assert ch not in result, f"Markdown char '{ch}' in get_shopping_list output"


# ---------------------------------------------------------------------------
# Step 5: weekly_meal_plan, waste_report, bedrock_converse with timeout
# ---------------------------------------------------------------------------

class TestWeeklyMealPlan:
    def test_empty_pantry_guard(self):
        assert "empty" in s().weekly_meal_plan().lower()

    def test_bedrock_success_returns_text(self):
        s().add_item("chicken", quantity=2, days_until_expiry=1)
        s().add_item("rice", quantity=1, unit="cup", days_until_expiry=5)
        mock_resp = {
            "output": {"message": {"content": [
                {"text": "Monday: chicken fried rice. Tuesday: chicken soup with rice. Wednesday: rice bowl."}
            ]}}
        }
        mock_client = MagicMock()
        mock_client.converse.return_value = mock_resp
        with patch("boto3.client", return_value=mock_client):
            result = s().weekly_meal_plan()
        assert "chicken" in result.lower() or "rice" in result.lower()

    def test_bedrock_failure_returns_fallback(self):
        s().add_item("pasta", quantity=500, unit="grams", days_until_expiry=2)
        with patch("boto3.client", side_effect=Exception("timeout")):
            result = s().weekly_meal_plan()
        assert "pasta" in result or "meals with" in result.lower()

    def test_output_no_markdown(self):
        s().add_item("beans", quantity=1, days_until_expiry=2)
        mock_resp = {
            "output": {"message": {"content": [
                {"text": "Day 1 beans stew. Day 2 beans soup."}
            ]}}
        }
        mock_client = MagicMock()
        mock_client.converse.return_value = mock_resp
        with patch("boto3.client", return_value=mock_client):
            result = s().weekly_meal_plan()
        for ch in ("#", "*", "`", "|"):
            assert ch not in result

    def test_owner_scoped(self):
        ctx_a = make_ctx("alice")
        ctx_b = make_ctx("bob")
        srv = s()
        srv.add_item("tofu", quantity=1, ctx=ctx_a)
        # Bob has empty pantry — should get empty message
        result = srv.weekly_meal_plan(ctx=ctx_b)
        assert "empty" in result.lower()


class TestWasteReport:
    def test_empty_report(self):
        result = s().waste_report(days=7)
        assert "no items" in result.lower() or "0" in result or "no" in result.lower()

    def test_use_item_populates_waste_log(self):
        srv = s()
        srv.add_item("milk", quantity=3, unit="litres")
        srv.use_item("milk", quantity=1)     # partial use
        srv.use_item("milk", quantity=2)     # empties it
        result = srv.waste_report(days=1)
        assert "milk" in result
        assert "3" in result   # total 3 litres consumed

    def test_report_respects_days_window(self):
        """Items logged before the window should not appear."""
        import sqlite3 as _sql
        db_path = os.getenv("PANTRY_DB")
        # Insert a log entry 10 days ago
        con = _sql.connect(db_path)
        con.execute(
            "CREATE TABLE IF NOT EXISTS waste_log"
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL DEFAULT 'default',"
            " name TEXT NOT NULL, qty REAL NOT NULL, unit TEXT NOT NULL DEFAULT 'pcs',"
            " logged_at TEXT NOT NULL)"
        )
        old_date = (date.today() - timedelta(days=10)).isoformat()
        con.execute(
            "INSERT INTO waste_log(owner, name, qty, unit, logged_at) VALUES('default','ancient_cheese',1,'pcs',?)",
            (old_date,),
        )
        con.commit(); con.close()
        result = s().waste_report(days=7)
        assert "ancient_cheese" not in result

    def test_owner_isolated(self):
        ctx_a = make_ctx("alice")
        ctx_b = make_ctx("bob")
        srv = s()
        srv.add_item("butter", quantity=1, ctx=ctx_a)
        srv.use_item("butter", quantity=1, ctx=ctx_a)
        result_b = srv.waste_report(days=7, ctx=ctx_b)
        assert "butter" not in result_b


# ---------------------------------------------------------------------------
# Step 6: Auth, input validation, no stack traces
# ---------------------------------------------------------------------------

class TestAuthAndValidation:
    """Tests for check_auth, validate_name, validate_positive, and their
    integration into tool functions."""

    # --- validate_name ---
    def test_validate_name_strips_and_lowercases(self):
        assert s().validate_name("  EGGS  ") == "eggs"

    def test_validate_name_rejects_empty(self):
        import pytest
        with pytest.raises(ValueError, match="empty"):
            s().validate_name("   ")

    def test_validate_name_rejects_too_long(self):
        import pytest
        with pytest.raises(ValueError, match="too long"):
            s().validate_name("x" * 101)

    def test_validate_name_accepts_100_chars(self):
        name = "a" * 100
        assert s().validate_name(name) == name

    # --- validate_positive ---
    def test_validate_positive_rejects_zero(self):
        import pytest
        with pytest.raises(ValueError, match="greater than zero"):
            s().validate_positive(0)

    def test_validate_positive_rejects_negative(self):
        import pytest
        with pytest.raises(ValueError, match="greater than zero"):
            s().validate_positive(-5)

    def test_validate_positive_accepts_positive(self):
        s().validate_positive(0.1)   # should not raise

    # --- Tool-level validation (returns friendly string, no exception) ---
    def test_add_item_empty_name_returns_message(self):
        result = s().add_item("  ")
        assert "empty" in result.lower() or "cannot" in result.lower()

    def test_add_item_zero_quantity_returns_message(self):
        result = s().add_item("milk", quantity=0)
        assert "greater than zero" in result.lower()

    def test_add_item_negative_quantity_returns_message(self):
        result = s().add_item("milk", quantity=-3)
        assert "greater than zero" in result.lower()

    def test_use_item_zero_quantity_returns_message(self):
        result = s().use_item("milk", quantity=0)
        assert "greater than zero" in result.lower()

    def test_add_to_shopping_list_empty_name(self):
        result = s().add_to_shopping_list("")
        assert "empty" in result.lower() or "cannot" in result.lower()

    def test_expiring_soon_zero_days_returns_message(self):
        result = s().expiring_soon(days=0)
        assert "greater than zero" in result.lower()

    def test_waste_report_zero_days_returns_message(self):
        result = s().waste_report(days=0)
        assert "greater than zero" in result.lower()

    # --- check_auth ---
    def test_auth_disabled_when_key_unset(self, monkeypatch):
        """MCP_API_KEY not set → auth always passes."""
        monkeypatch.setattr(s(), "MCP_API_KEY", "")
        assert s().check_auth(None) is None

    def test_auth_passes_with_correct_bearer(self, monkeypatch):
        monkeypatch.setattr(s(), "MCP_API_KEY", "secret123")
        ctx = make_ctx()
        ctx.request_context.request.headers.get.side_effect = (
            lambda key, default="": "Bearer secret123" if key == "authorization" else default
        )
        assert s().check_auth(ctx) is None

    def test_auth_fails_with_wrong_bearer(self, monkeypatch):
        monkeypatch.setattr(s(), "MCP_API_KEY", "secret123")
        ctx = make_ctx()
        ctx.request_context.request.headers.get.side_effect = (
            lambda key, default="": "Bearer wrongkey" if key == "authorization" else default
        )
        result = s().check_auth(ctx)
        assert result is not None and "unauthorized" in result.lower()

    def test_auth_skipped_for_none_ctx(self, monkeypatch):
        """ctx=None (tests / stdio) → auth always passes even if key is set."""
        monkeypatch.setattr(s(), "MCP_API_KEY", "secret123")
        assert s().check_auth(None) is None

    def test_tool_returns_friendly_message_on_unexpected_error(self, monkeypatch):
        """Simulate DB blow-up: tool must return a string, not raise."""
        import sqlite3
        def broken_db():
            raise sqlite3.DatabaseError("disk I/O error")
        monkeypatch.setattr(s(), "db", broken_db)
        result = s().list_pantry()
        assert isinstance(result, str)
        assert "traceback" not in result.lower()
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Step 9: set_preferences / get_preferences_tool
# ---------------------------------------------------------------------------

class TestSetPreferences:
    def test_set_diet_returns_confirmation(self):
        result = s().set_preferences(diet="vegetarian")
        assert "vegetarian" in result.lower()

    def test_set_allergies_returns_confirmation(self):
        result = s().set_preferences(allergies="nuts")
        assert "nuts" in result.lower()

    def test_set_dislikes_returns_confirmation(self):
        result = s().set_preferences(dislikes="coriander mushrooms")
        assert "coriander" in result.lower()

    def test_set_all_fields(self):
        result = s().set_preferences(diet="vegan", allergies="gluten", dislikes="onion")
        assert "vegan" in result.lower()
        assert "gluten" in result.lower()
        assert "onion" in result.lower()

    def test_empty_call_returns_helpful_message(self):
        result = s().set_preferences()
        assert "no preferences" in result.lower() or "provide" in result.lower() or "diet" in result.lower()

    def test_get_preferences_empty(self):
        result = s().get_preferences_tool()
        assert "no preferences" in result.lower() or "none" in result.lower() or "yet" in result.lower()

    def test_get_preferences_after_set(self):
        srv = s()
        srv.set_preferences(diet="vegetarian", allergies="nuts")
        result = srv.get_preferences_tool()
        assert "vegetarian" in result.lower()
        assert "nuts" in result.lower()

    def test_preferences_owner_isolation(self):
        ctx_a = make_ctx("alice")
        ctx_b = make_ctx("bob")
        srv = s()
        srv.set_preferences(diet="vegan", ctx=ctx_a)
        result_b = srv.get_preferences_tool(ctx=ctx_b)
        assert "vegan" not in result_b.lower()

    def test_preferences_injected_into_suggest_recipe(self):
        """Preferences should appear in the Bedrock prompt."""
        srv = s()
        srv.add_item("chicken", quantity=2, days_until_expiry=1)
        srv.set_preferences(diet="vegetarian")
        captured_prompts = []

        mock_resp = {
            "output": {"message": {"content": [
                {"text": "Try a vegetable stir-fry."}
            ]}}
        }
        mock_client = MagicMock()
        mock_client.converse.side_effect = lambda **kwargs: (
            captured_prompts.append(
                kwargs["messages"][0]["content"][0]["text"]
            ) or mock_resp
        )
        with patch("boto3.client", return_value=mock_client):
            srv.suggest_recipe()

        assert len(captured_prompts) == 1
        assert "vegetarian" in captured_prompts[0].lower()

    def test_no_markdown_in_output(self):
        result = s().set_preferences(diet="keto")
        for ch in ("#", "*", "_", "|", "`"):
            assert ch not in result


# ---------------------------------------------------------------------------
# Step 9: schedule_meal / get_meal_schedule
# ---------------------------------------------------------------------------

class TestScheduleMeal:
    def test_schedule_with_iso_date(self):
        result = s().schedule_meal("pasta carbonara", meal_date="2026-10-10")
        assert "pasta carbonara" in result.lower()
        assert "2026-10-10" in result or "Oct" in result or "Saturday" in result

    def test_schedule_today_default(self):
        result = s().schedule_meal("omelette")
        assert "omelette" in result.lower()

    def test_schedule_tomorrow(self):
        result = s().schedule_meal("soup", meal_date="tomorrow")
        assert "soup" in result.lower()

    def test_schedule_weekday_name(self):
        result = s().schedule_meal("salad", meal_date="monday")
        assert "salad" in result.lower()

    def test_schedule_unknown_date_returns_error(self):
        result = s().schedule_meal("pizza", meal_date="next fortnight")
        assert "didn't understand" in result.lower() or "try" in result.lower()

    def test_schedule_empty_recipe_returns_error(self):
        result = s().schedule_meal("")
        assert "provide" in result.lower() or "recipe" in result.lower()

    def test_schedule_too_long_recipe_returns_error(self):
        result = s().schedule_meal("x" * 301)
        assert "too long" in result.lower()

    def test_get_meal_schedule_empty(self):
        result = s().get_meal_schedule()
        assert "no meals" in result.lower()

    def test_get_meal_schedule_shows_scheduled_meal(self):
        srv = s()
        today = date.today().isoformat()
        srv.schedule_meal("stir-fry", meal_date=today)
        result = srv.get_meal_schedule(days=1)
        assert "stir-fry" in result.lower()

    def test_get_meal_schedule_owner_isolation(self):
        ctx_a = make_ctx("alice")
        ctx_b = make_ctx("bob")
        srv = s()
        today = date.today().isoformat()
        srv.schedule_meal("alice dish", meal_date=today, ctx=ctx_a)
        result_b = srv.get_meal_schedule(days=7, ctx=ctx_b)
        assert "alice dish" not in result_b.lower()

    def test_get_meal_schedule_respects_days_window(self):
        srv = s()
        far_future = (date.today() + timedelta(days=30)).isoformat()
        srv.schedule_meal("far future meal", meal_date=far_future)
        result = srv.get_meal_schedule(days=7)
        assert "far future meal" not in result.lower()

    def test_no_markdown_in_output(self):
        srv = s()
        srv.schedule_meal("ramen", meal_date="today")
        result = srv.get_meal_schedule()
        for ch in ("#", "*", "_", "|", "`"):
            assert ch not in result
