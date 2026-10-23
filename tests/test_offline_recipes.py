"""Tests for the offline recipe book used when Bedrock is unavailable."""

from datetime import date, timedelta
from unittest.mock import patch

import offline_recipes as orc

TODAY = date(2026, 10, 22)


def pantry(*items):
    """items: (name, days_until_expiry or None) → rows shaped like sqlite rows."""
    return [
        {"name": n, "expires": (TODAY + timedelta(days=d)).isoformat() if d is not None else None}
        for n, d in items
    ]


class TestSuggest:
    def test_uses_soonest_expiring_item(self):
        rows = pantry(("spinach", 1), ("eggs", 2), ("chicken", 20))
        result = orc.suggest(rows, {}, today=TODAY)
        assert "omelette" in result
        assert "spinach" in result and "eggs" in result
        assert "uses up your eggs and spinach before they go bad" in result

    def test_expiry_note_only_names_items_it_uses(self):
        rows = pantry(("bread", 2), ("cheese", 9), ("lentils", None))
        result = orc.suggest(rows, {}, today=TODAY)
        assert "uses up your bread before it goes bad." in result
        assert "lentil" not in result

    def test_vegetarian_never_gets_meat(self):
        rows = pantry(("chicken", 1), ("potatoes", 1), ("eggs", 5))
        result = orc.suggest(rows, {"diet": "vegetarian"}, today=TODAY)
        assert "chicken" not in result.lower()
        assert "potato" in result.lower() or "egg" in result.lower()

    def test_vegan_excludes_eggs_and_dairy(self):
        rows = pantry(("eggs", 1), ("cheese", 1), ("tomatoes", 2), ("pasta", None))
        result = orc.suggest(rows, {"diet": "vegan"}, today=TODAY)
        assert "tomato pasta" in result

    def test_dislikes_and_allergies_respected(self):
        rows = pantry(("mushrooms", 1), ("pasta", 2), ("tomatoes", 3))
        result = orc.suggest(rows, {"dislikes": "mushrooms"}, today=TODAY)
        assert "mushroom" not in result.lower()
        rows = pantry(("eggs", 1), ("bread", 1), ("milk", 1))
        result = orc.suggest(rows, {"allergies": "eggs"}, today=TODAY)
        assert result is None or "egg" not in result.lower()

    def test_mood_quick_prefers_short_recipe(self):
        rows = pantry(("chicken", 2), ("potatoes", 2), ("lemon", 2), ("bread", 2), ("cheese", 2))
        result = orc.suggest(rows, {}, mood="something quick", today=TODAY)
        assert "toastie" in result

    def test_multiword_and_plural_names_match(self):
        rows = pantry(("cherry tomatoes", 1), ("spaghetti", 4))
        assert "tomato pasta" in orc.suggest(rows, {}, today=TODAY)

    def test_no_false_substring_match(self):
        # "eggplant" must not count as eggs
        assert orc.suggest(pantry(("eggplant", 1)), {}, today=TODAY) is None

    def test_no_match_returns_none(self):
        assert orc.suggest(pantry(("dragonfruit", 1)), {}, today=TODAY) is None

    def test_main_ingredient_required(self):
        # Tomatoes and spinach alone must not suggest lentil dal or chickpea curry
        result = orc.suggest(pantry(("tomatoes", 1), ("spinach", 1)), {}, today=TODAY)
        assert result is None or ("dal" not in result and "chickpea" not in result)

    def test_item_expiring_tomorrow_wins(self):
        rows = pantry(("spinach", 1), ("eggs", 10), ("cheese", 12), ("tomatoes", 2), ("bread", 4))
        assert "omelette" in orc.suggest(rows, {}, today=TODAY)

    def test_named_ingredients_required(self):
        # Pasta and cheese must not become "creamy mushroom pasta"
        result = orc.week_plan(pantry(("pasta", 1), ("cheese", 1), ("milk", 1)), {}, today=TODAY)
        assert result is None or "mushroom" not in result

    def test_voice_first_output(self):
        result = orc.suggest(pantry(("eggs", 1), ("bread", 1)), {}, today=TODAY)
        for ch in ("#", "*", "`", "|", "\n"):
            assert ch not in result


class TestWeekPlan:
    def test_distinct_dinners_starting_tonight(self):
        rows = pantry(("eggs", 1), ("bread", 2), ("milk", 2), ("pasta", 3), ("tomatoes", 3),
                      ("chicken", 4), ("rice", 9), ("potatoes", 6), ("onions", 9))
        result = orc.week_plan(rows, {}, today=TODAY)
        assert result.startswith("Here's your week. Tonight,")
        assert "Tomorrow," in result and "Saturday," in result  # 2026-10-24
        nights = result.split(". ")[1:]
        assert len(nights) == len(set(nights))

    def test_short_pantry_says_so(self):
        result = orc.week_plan(pantry(("bananas", 1), ("eggs", 3)), {}, today=TODAY)
        assert "Add a few more groceries" in result

    def test_no_match_returns_none(self):
        assert orc.week_plan(pantry(("dragonfruit", 1)), {}, today=TODAY) is None


class TestServerIntegration:
    def test_suggest_recipe_uses_recipe_book_when_bedrock_fails(self):
        import server
        server.add_item("spinach", quantity=1, days_until_expiry=1)
        server.add_item("eggs", quantity=6, days_until_expiry=3)
        with patch("boto3.client", side_effect=Exception("blocked")):
            result = server.suggest_recipe()
        assert result.startswith("How about a spinach and cheese omelette?")
        assert server.BEDROCK_STATUS == "fallback: Exception"

    def test_vegetarian_preference_reaches_recipe_book(self):
        import server
        server.add_item("chicken", quantity=2, days_until_expiry=1)
        server.add_item("potatoes", quantity=4, days_until_expiry=2)
        server.add_item("eggs", quantity=6, days_until_expiry=5)
        server.set_preferences(diet="vegetarian")
        with patch("boto3.client", side_effect=Exception("blocked")):
            result = server.suggest_recipe()
        assert "chicken" not in result.lower()

    def test_bedrock_status_ok_after_success(self):
        import server
        server.add_item("eggs", quantity=6)
        resp = {"output": {"message": {"content": [{"text": "Make an omelette."}]}}}
        with patch("boto3.client") as client:
            client.return_value.converse.return_value = resp
            server.suggest_recipe()
        assert server.BEDROCK_STATUS == "ok"
