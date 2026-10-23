"""
Offline recipe book: used when Amazon Bedrock is unavailable.

Picks recipes that use the soonest-expiring pantry items, respects dietary
preferences, and answers in short, speakable sentences. No network calls,
no extra dependencies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

# Which recipe diets each user diet allows.
_DIET_ALLOWS = {
    "vegan": {"vegan"},
    "vegetarian": {"vegan", "vegetarian"},
    "pescatarian": {"vegan", "vegetarian", "pescatarian"},
}

# Dietary words that rule out a tag rather than an ingredient.
_TAG_EXCLUSIONS = {"gluten": "gluten", "dairy": "dairy", "fish": "fish", "seafood": "fish"}


@dataclass(frozen=True)
class Recipe:
    name: str                               # spoken form, with article
    ingredients: tuple[tuple[str, ...], ...]  # each ingredient: synonyms
    diet: str                               # vegan | vegetarian | pescatarian | meat
    minutes: int
    method: str                             # one or two short sentences
    tags: frozenset[str] = frozenset()
    need: int = 2                           # the first `need` ingredients are required


def _r(name, ingredients, diet, minutes, method, *tags, need=2) -> Recipe:
    return Recipe(name, tuple(tuple(i.split("|")) for i in ingredients), diet, minutes, method, frozenset(tags), need)


RECIPES: tuple[Recipe, ...] = (
    _r("a spinach and cheese omelette", ["egg", "spinach", "cheese"], "vegetarian", 10,
       "Whisk the eggs, wilt the spinach in a hot pan, pour the eggs over, add cheese if you have some, and fold.",
       "breakfast", "quick", "dairy"),
    _r("French toast", ["egg", "bread|toast|brioche", "milk"], "vegetarian", 15,
       "Dip the bread in egg beaten with milk, then fry until golden on both sides.",
       "breakfast", "gluten", "dairy"),
    _r("egg fried rice", ["egg", "rice", "onion|spring onion", "pea|carrot"], "vegetarian", 15,
       "Fry the onion, add cold cooked rice and vegetables, scramble in the eggs, and finish with soy sauce.",
       "quick", "asian"),
    _r("a simple tomato pasta", ["pasta|spaghetti|penne|macaroni", "tomato", "onion|garlic", "basil"], "vegan", 20,
       "Boil the pasta. Meanwhile soften onion and garlic in oil, add chopped tomatoes, simmer ten minutes and toss together.",
       "italian", "gluten"),
    _r("a creamy mushroom pasta", ["pasta|spaghetti|penne", "mushroom", "cream|milk", "cheese|parmesan"], "vegetarian", 20,
       "Brown the mushrooms with garlic, stir in cream and cheese, and toss with the boiled pasta.",
       "italian", "gluten", "dairy"),
    _r("a chicken stir-fry", ["chicken", "pepper|capsicum", "broccoli", "onion", "rice|noodle"], "meat", 20,
       "Slice the chicken thin, stir-fry it over high heat, add the vegetables and a splash of soy sauce, and serve over rice.",
       "quick", "asian", need=1),
    _r("a tofu and vegetable stir-fry", ["tofu", "broccoli", "pepper|capsicum", "carrot", "rice|noodle"], "vegan", 15,
       "Crisp the tofu cubes in a hot pan, add the vegetables and soy sauce, and serve over rice or noodles.",
       "quick", "asian", "healthy", need=1),
    _r("roast lemon chicken with potatoes", ["chicken", "potato", "lemon"], "meat", 35,
       "Toss chicken pieces and halved potatoes with oil, lemon and herbs, and roast at 220 degrees for about 30 minutes."),
    _r("a chicken curry", ["chicken", "onion", "tomato", "yogurt|yoghurt|cream"], "meat", 30,
       "Fry onion with curry spices, add the chicken and tomatoes, simmer 20 minutes, and stir in yogurt at the end.",
       "indian", need=1),
    _r("a red lentil dal", ["lentil|dal", "onion", "tomato", "spinach"], "vegan", 30,
       "Simmer the lentils with turmeric until soft, then stir in onion and tomato fried with cumin and garlic.",
       "indian", "healthy", need=1),
    _r("a chickpea curry", ["chickpea|chana", "onion", "tomato", "spinach"], "vegan", 25,
       "Fry onion with garlic and curry spices, add tomatoes and chickpeas, and simmer 15 minutes.",
       "indian", "healthy", need=1),
    _r("cheese quesadillas", ["tortilla|wrap", "cheese", "bean", "pepper|capsicum"], "vegetarian", 10,
       "Fill a tortilla with cheese, plus any beans or peppers, fold it, and toast in a dry pan until crisp.",
       "quick", "mexican", "gluten", "dairy"),
    _r("a potato and egg hash", ["potato", "egg", "onion", "pepper|capsicum"], "vegetarian", 25,
       "Fry diced potato and onion until crisp, make a few wells, crack in the eggs and cover until set.",
       "breakfast"),
    _r("a tuna pasta salad", ["tuna", "pasta|penne|macaroni", "corn|sweetcorn", "mayo|mayonnaise"], "pescatarian", 15,
       "Boil and cool the pasta, then mix with tuna, sweetcorn and mayonnaise.",
       "quick", "gluten", "fish"),
    _r("pan-fried salmon with greens", ["salmon", "broccoli|spinach|green bean|asparagus", "lemon"], "pescatarian", 20,
       "Pan-fry the salmon skin side down, steam the greens, and finish with a squeeze of lemon.",
       "healthy", "fish", need=1),
    _r("banana pancakes", ["banana", "egg"], "vegetarian", 10,
       "Mash a banana with two eggs and fry small pancakes in a buttered pan.",
       "breakfast", "quick"),
    _r("a banana oat smoothie", ["banana", "milk|yogurt|yoghurt", "oat"], "vegetarian", 5,
       "Blend the banana with milk or yogurt and a handful of oats.",
       "breakfast", "quick", "dairy"),
    _r("a Greek salad", ["cucumber", "tomato", "feta|cheese", "onion", "olive"], "vegetarian", 10,
       "Chop the cucumber, tomato and onion, top with feta and olives, and dress with olive oil.",
       "quick", "healthy", "dairy"),
    _r("a cheese toastie", ["bread|toast", "cheese", "tomato"], "vegetarian", 10,
       "Layer cheese, and sliced tomato if you have it, between bread and toast in a pan until golden.",
       "quick", "gluten", "dairy"),
    _r("a yogurt and fruit bowl", ["yogurt|yoghurt", "berry|berries|strawberry|strawberries|blueberry|blueberries|apple|banana", "oat|granola"], "vegetarian", 5,
       "Spoon yogurt into a bowl and top with fruit and a handful of oats or granola.",
       "breakfast", "quick", "dairy", need=1),
    _r("a vegetable soup", ["carrot", "potato", "onion", "celery|leek", "tomato"], "vegan", 30,
       "Soften the chopped vegetables in oil, cover with stock, simmer 20 minutes, and blend if you like.",
       "healthy", need=1),
)


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------

def _word_match(keyword: str, text: str) -> bool:
    """True if keyword appears in text as a word, allowing a plural ending."""
    return re.search(rf"\b{re.escape(keyword)}(?:s|es)?\b", text) is not None


def _days_left(expires: str | None, today: date) -> int | None:
    if not expires:
        return None
    try:
        return (date.fromisoformat(expires) - today).days
    except ValueError:
        return None


def _avoid_words(prefs: dict) -> list[str]:
    raw = f"{prefs.get('dislikes', '')} {prefs.get('allergies', '')}".lower()
    words = re.split(r"[\s,;/]+|\band\b", raw)
    return [w.rstrip("s") for w in words if len(w) > 2]


def _allowed(recipe: Recipe, prefs: dict) -> bool:
    diet = (prefs.get("diet") or "").lower()
    for key, allowed in _DIET_ALLOWS.items():
        if key in diet and recipe.diet not in allowed:
            return False
    if "gluten" in diet and "gluten" in recipe.tags:
        return False
    if "dairy" in diet and "dairy" in recipe.tags:
        return False
    keywords = [k for ing in recipe.ingredients for k in ing]
    for word in _avoid_words(prefs):
        if _TAG_EXCLUSIONS.get(word) in recipe.tags or (word == "meat" and recipe.diet == "meat"):
            return False
        if word in recipe.name.lower() or any(k.startswith(word) or word.startswith(k) for k in keywords):
            return False
    return True


def _weight(days_left: int | None) -> float:
    """Soon-expiring items count more, so they get used first."""
    if days_left is None:
        return 0.5
    if days_left <= 1:
        return 5.0
    if days_left <= 3:
        return 3.0
    if days_left <= 5:
        return 2.0
    return 1.0


def _rank(rows, prefs: dict, mood: str, today: date) -> list[tuple[float, Recipe, list[str], list[str]]]:
    """Score allowed recipes against the pantry. Returns (score, recipe, used items, expiring items used)."""
    pantry = [(r["name"].lower(), _days_left(r["expires"], today)) for r in rows]
    mood_words = set(re.findall(r"[a-z]+", mood.lower()))
    ranked = []
    for recipe in RECIPES:
        if not _allowed(recipe, prefs):
            continue
        used, expiring, score, missing_key = [], [], 0.0, False
        for i, synonyms in enumerate(recipe.ingredients):
            match = next(((n, d) for n, d in pantry
                          if n not in used and any(_word_match(k, n) for k in synonyms)), None)
            if match is None:
                if i < recipe.need:
                    missing_key = True  # no mushroom pasta without mushrooms
                    break
                continue
            name, days = match
            used.append(name)
            score += _weight(days)
            if days is not None and days <= 3:
                expiring.append(name)
        if missing_key:
            continue
        if mood_words & recipe.tags:
            score += 2
        if mood_words & {"quick", "fast", "easy"} and recipe.minutes <= 15:
            score += 2
        ranked.append((score, recipe, used, expiring))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return ranked


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def suggest(rows, prefs: dict, mood: str = "", today: date | None = None) -> str | None:
    """One voice-friendly recipe suggestion, or None if nothing in the pantry matches."""
    ranked = _rank(rows, prefs, mood, today or date.today())
    if not ranked:
        return None
    _, recipe, used, expiring = ranked[0]
    reply = f"How about {recipe.name}? It takes about {recipe.minutes} minutes"
    if expiring:
        plural = len(expiring) > 1 or expiring[0].endswith("s")
        reply += f" and uses up your {_join(expiring)} before {'they go' if plural else 'it goes'} bad."
    else:
        reply += f" and uses your {_join(used)}."
    return f"{reply} Here's how: {recipe.method}"


def week_plan(rows, prefs: dict, today: date | None = None) -> str | None:
    """Up to seven different dinners, soonest-expiring first, or None if nothing matches."""
    today = today or date.today()
    picks = [recipe for _, recipe, _, _ in _rank(rows, prefs, "", today)][:7]
    if not picks:
        return None
    nights = []
    for i, recipe in enumerate(picks):
        day = "Tonight" if i == 0 else "Tomorrow" if i == 1 else (today + timedelta(days=i)).strftime("%A")
        name = re.sub(r"^(?:a|an)\s+", "", recipe.name)
        nights.append(f"{day}, {name}.")
    plan = "Here's your week. " + " ".join(nights)
    if len(picks) < 7:
        plan += " Add a few more groceries and I'll fill in the rest of the week."
    return plan
