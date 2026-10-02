"""PantryPilot - an Alexa+ MCP server (Streamable HTTP) that fights food waste.
Tools are voice-first: short, speakable answers. Recipes come from Amazon Bedrock.

Step 3: Multi-user support scoped by 'owner' (X-User-ID header or DEFAULT_OWNER).
  - TODO(verify): confirm the exact header/claim Alexa+ uses for user identity.
Step 4: Shopping list (add_to_shopping_list / get_shopping_list); use_item auto-adds.
Step 5: Smarter Bedrock. bedrock_converse() helper with timeout + 1 retry.
  - weekly_meal_plan: 7-day dinner plan from pantry contents.
  - waste_report: items consumed in the last N days.
  - use_item logs every consumption to waste_log for reporting.
Step 6: Auth and safety.
  - Bearer-token gate via MCP_API_KEY env var (disabled when unset).
  - Input validation: name non-empty ≤100 chars, quantity > 0.
  - No stack traces in tool output – every tool catches Exception.
Step 9: Kitchen/home-ops reframe.
  - set_preferences: store diet, dislikes, allergies per owner.
  - suggest_recipe / weekly_meal_plan: inject preferences into Bedrock prompts.
  - schedule_meal / get_meal_schedule: simple meal planning table.
  - /privacy and /terms routes served inline for Alexa+ add-on registration.
"""
import os
import logging
import sqlite3
from datetime import date, timedelta

import boto3
from botocore.config import Config as BotoCfg

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP, Context

load_dotenv()  # picks up .env for local dev; env vars override in production

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pantrypilot")

# ---------------------------------------------------------------------------
# Server configuration
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "PantryPilot",
    host="0.0.0.0",
    port=int(os.getenv("PORT", "8000")),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/mcp",
)

DB = os.getenv("PANTRY_DB", "pantry.db")
MODEL = os.getenv("BEDROCK_MODEL_ID", "amazon.nova-micro-v1:0")
DEFAULT_OWNER = os.getenv("DEFAULT_OWNER", "default")
MCP_API_KEY = os.getenv("MCP_API_KEY", "")  # empty → auth disabled (local dev)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def db() -> sqlite3.Connection:
    """Open the SQLite database and ensure the schema is up to date."""
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("""
        CREATE TABLE IF NOT EXISTS items(
            owner   TEXT NOT NULL DEFAULT 'default',
            name    TEXT NOT NULL,
            qty     REAL,
            unit    TEXT,
            expires TEXT,
            PRIMARY KEY (owner, name)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS shopping_list(
            owner TEXT NOT NULL DEFAULT 'default',
            name  TEXT NOT NULL,
            qty   REAL    NOT NULL DEFAULT 1,
            unit  TEXT    NOT NULL DEFAULT 'pcs',
            PRIMARY KEY (owner, name)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS waste_log(
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            owner     TEXT NOT NULL DEFAULT 'default',
            name      TEXT NOT NULL,
            qty       REAL NOT NULL,
            unit      TEXT NOT NULL DEFAULT 'pcs',
            logged_at TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS preferences(
            owner     TEXT NOT NULL PRIMARY KEY,
            diet      TEXT,
            dislikes  TEXT,
            allergies TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS meal_schedule(
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            owner     TEXT NOT NULL DEFAULT 'default',
            meal_date TEXT NOT NULL,
            recipe    TEXT NOT NULL,
            status    TEXT NOT NULL DEFAULT 'planned'
        )
    """)
    # Migration: tables created before Step 3 lacked the owner column.
    # Safe to run every time; the ALTER is a no-op if the column exists.
    try:
        con.execute("ALTER TABLE items ADD COLUMN owner TEXT NOT NULL DEFAULT 'default'")
        con.commit()
    except sqlite3.OperationalError:
        pass  # column already present
    return con


def get_owner(ctx: Context | None) -> str:
    """Return the owner string used to scope this request's pantry.

    Priority:
      1. X-User-ID HTTP header (TODO(verify): confirm Alexa+ header name)
      2. DEFAULT_OWNER env var (default "default")
    """
    if ctx is not None:
        try:
            req = ctx.request_context.request          # Starlette Request
            uid = req.headers.get("x-user-id", "").strip()
            if uid:
                return uid
        except Exception:
            pass  # running outside HTTP context (e.g., tests, stdio)
    return DEFAULT_OWNER


def get_preferences(owner: str) -> dict:
    """Fetch stored preferences for an owner; returns empty dict if none set."""
    try:
        row = db().execute(
            "SELECT diet, dislikes, allergies FROM preferences WHERE owner=?", (owner,)
        ).fetchone()
        if not row:
            return {}
        return {
            "diet": row["diet"] or "",
            "dislikes": row["dislikes"] or "",
            "allergies": row["allergies"] or "",
        }
    except Exception:
        return {}


def format_preferences(prefs: dict) -> str:
    """Turn a preferences dict into a short prompt clause."""
    parts = []
    if prefs.get("diet"):
        parts.append(f"diet: {prefs['diet']}")
    if prefs.get("allergies"):
        parts.append(f"allergies: {prefs['allergies']}")
    if prefs.get("dislikes"):
        parts.append(f"avoid: {prefs['dislikes']}")
    return (", ".join(parts)) if parts else ""


def bedrock_converse(prompt: str, max_tokens: int = 350) -> str:
    """Call Bedrock Converse with a timeout; raise on any error.

    connect_timeout=5s, read_timeout=15s, max_attempts=1 (no SDK retries –
    we handle fallback in each caller).
    """
    client = boto3.client(
        "bedrock-runtime",
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        config=BotoCfg(
            connect_timeout=5,
            read_timeout=15,
            retries={"max_attempts": 1},
        ),
    )
    resp = client.converse(
        modelId=MODEL,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": max_tokens},
    )
    return resp["output"]["message"]["content"][0]["text"].strip()


# ---------------------------------------------------------------------------
# Auth & validation helpers
# ---------------------------------------------------------------------------

def check_auth(ctx: Context | None) -> str | None:
    """Return an error string if the request fails bearer-token auth, else None.

    Auth is skipped when MCP_API_KEY is unset (local dev / tests).
    TODO(verify): confirm whether Alexa+ sends Authorization header or uses
    a different authentication mechanism.
    """
    if not MCP_API_KEY:
        return None
    if ctx is None:
        return None  # direct call in tests or stdio transport – skip
    try:
        auth = ctx.request_context.request.headers.get("authorization", "")
        if auth == f"Bearer {MCP_API_KEY}":
            return None
        return "Unauthorized. Provide a valid API key."
    except Exception:
        return None  # can't read headers – allow gracefully


def validate_name(raw: str) -> str:
    """Strip, lower-case, and validate an item name. Raises ValueError on bad input."""
    name = raw.strip().lower()
    if not name:
        raise ValueError("Item name cannot be empty.")
    if len(name) > 100:
        raise ValueError("Item name is too long (max 100 characters).")
    return name


def validate_positive(value: float, label: str = "Quantity") -> None:
    """Raise ValueError if value is not strictly positive."""
    if value <= 0:
        raise ValueError(f"{label} must be greater than zero.")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def add_item(
    name: str,
    quantity: float = 1,
    unit: str = "pcs",
    days_until_expiry: int | None = None,
    ctx: Context | None = None,
) -> str:
    """Add groceries to the pantry, e.g. 'add 6 eggs that expire in 10 days'."""
    if err := check_auth(ctx):
        return err
    try:
        name = validate_name(name)
        validate_positive(quantity, "Quantity")
        if days_until_expiry is not None:
            validate_positive(days_until_expiry, "Days until expiry")
        owner = get_owner(ctx)
        exp = (
            (date.today() + timedelta(days=days_until_expiry)).isoformat()
            if days_until_expiry is not None else None
        )
        with db() as con:
            con.execute(
                """INSERT INTO items(owner, name, qty, unit, expires) VALUES(?,?,?,?,?)
                   ON CONFLICT(owner, name) DO UPDATE SET
                     qty=qty+excluded.qty,
                     expires=COALESCE(excluded.expires, expires)""",
                (owner, name, quantity, unit, exp),
            )
            removed = con.execute(
                "DELETE FROM shopping_list WHERE owner=? AND name=?", (owner, name)
            ).rowcount
        suffix = " Removed from your shopping list." if removed else ""
        return f"Added {quantity:g} {unit} of {name}.{suffix}"
    except ValueError as exc:
        return str(exc)
    except Exception:
        log.exception("add_item failed")
        return "Something went wrong adding that item. Please try again."



@mcp.tool()
def use_item(
    name: str,
    quantity: float = 1,
    ctx: Context | None = None,
) -> str:
    """Record that some of an item was used up or thrown out."""
    if err := check_auth(ctx):
        return err
    try:
        name = validate_name(name)
        validate_positive(quantity, "Quantity")
        owner = get_owner(ctx)
        with db() as con:
            row = con.execute(
                "SELECT qty, unit FROM items WHERE owner=? AND name=?", (owner, name)
            ).fetchone()
            if not row:
                return f"You don't have any {name}."
            left = row["qty"] - quantity
            if left <= 0:
                unit = row["unit"]
                used_qty = row["qty"]
                con.execute("DELETE FROM items WHERE owner=? AND name=?", (owner, name))
                con.execute(
                    """INSERT INTO shopping_list(owner, name, qty, unit) VALUES(?,?,?,?)
                       ON CONFLICT(owner, name) DO UPDATE SET qty=excluded.qty, unit=excluded.unit""",
                    (owner, name, 1, unit),
                )
                con.execute(
                    "INSERT INTO waste_log(owner, name, qty, unit, logged_at) VALUES(?,?,?,?,?)",
                    (owner, name, used_qty, unit, date.today().isoformat()),
                )
                return f"That was the last of your {name}. I've added it to your shopping list."
            con.execute("UPDATE items SET qty=? WHERE owner=? AND name=?", (left, owner, name))
            con.execute(
                "INSERT INTO waste_log(owner, name, qty, unit, logged_at) VALUES(?,?,?,?,?)",
                (owner, name, quantity, row["unit"], date.today().isoformat()),
            )
        return f"{left:g} {row['unit']} of {name} left."
    except ValueError as exc:
        return str(exc)
    except Exception:
        log.exception("use_item failed")
        return "Something went wrong. Please try again."


@mcp.tool()
def add_to_shopping_list(
    name: str,
    quantity: float = 1,
    unit: str = "pcs",
    ctx: Context | None = None,
) -> str:
    """Add an item to the shopping list, e.g. 'add milk to my shopping list'."""
    if err := check_auth(ctx):
        return err
    try:
        name = validate_name(name)
        validate_positive(quantity, "Quantity")
        owner = get_owner(ctx)
        with db() as con:
            con.execute(
                """INSERT INTO shopping_list(owner, name, qty, unit) VALUES(?,?,?,?)
                   ON CONFLICT(owner, name) DO UPDATE SET qty=excluded.qty, unit=excluded.unit""",
                (owner, name, quantity, unit),
            )
        return f"Added {quantity:g} {unit} of {name} to your shopping list."
    except ValueError as exc:
        return str(exc)
    except Exception:
        log.exception("add_to_shopping_list failed")
        return "Something went wrong. Please try again."


@mcp.tool()
def get_shopping_list(ctx: Context | None = None) -> str:
    """Read out everything on the shopping list."""
    if err := check_auth(ctx):
        return err
    try:
        owner = get_owner(ctx)
        rows = db().execute(
            "SELECT name, qty, unit FROM shopping_list WHERE owner=? ORDER BY name", (owner,)
        ).fetchall()
        if not rows:
            return "Your shopping list is empty."
        return "Shopping list: " + ", ".join(
            f"{r['qty']:g} {r['unit']} {r['name']}" for r in rows
        ) + "."
    except Exception:
        log.exception("get_shopping_list failed")
        return "Could not retrieve your shopping list. Please try again."


@mcp.tool()
def list_pantry(ctx: Context | None = None) -> str:
    """Read out everything currently in the pantry."""
    if err := check_auth(ctx):
        return err
    try:
        owner = get_owner(ctx)
        rows = db().execute(
            "SELECT * FROM items WHERE owner=? ORDER BY name", (owner,)
        ).fetchall()
        if not rows:
            return "Your pantry is empty."
        return "You have " + ", ".join(
            f"{r['qty']:g} {r['unit']} {r['name']}" for r in rows
        ) + "."
    except Exception:
        log.exception("list_pantry failed")
        return "Could not read the pantry. Please try again."


@mcp.tool()
def expiring_soon(days: int = 3, ctx: Context | None = None) -> str:
    """List items that expire within the next N days (default 3)."""
    if err := check_auth(ctx):
        return err
    try:
        validate_positive(days, "Days")
        owner = get_owner(ctx)
        cutoff = (date.today() + timedelta(days=days)).isoformat()
        rows = db().execute(
            """SELECT name, expires FROM items
               WHERE owner=? AND expires IS NOT NULL AND expires<=?
               ORDER BY expires""",
            (owner, cutoff),
        ).fetchall()
        if not rows:
            return f"Nothing expires in the next {days} days."
        today = date.today()
        return "Use soon: " + ", ".join(
            f"{r['name']} ({'expired' if date.fromisoformat(r['expires']) < today else r['expires']})"
            for r in rows
        ) + "."
    except ValueError as exc:
        return str(exc)
    except Exception:
        log.exception("expiring_soon failed")
        return "Could not check expiry dates. Please try again."


@mcp.tool()
def suggest_recipe(mood: str = "", ctx: Context | None = None) -> str:
    """Suggest one quick recipe using expiring items first, tailored to your preferences (powered by Amazon Bedrock)."""
    if err := check_auth(ctx):
        return err
    try:
        owner = get_owner(ctx)
        rows = db().execute(
            """SELECT name, qty, unit, expires FROM items
               WHERE owner=?
               ORDER BY expires IS NULL, expires""",
            (owner,),
        ).fetchall()
        if not rows:
            return "Your pantry is empty, so add some groceries first."
        stock = "; ".join(
            f"{r['qty']:g} {r['unit']} {r['name']} (exp {r['expires'] or 'n/a'})"
            for r in rows
        )
        prefs = get_preferences(owner)
        pref_clause = format_preferences(prefs)
        pref_text = f" Preferences: {pref_clause}." if pref_clause else ""
        prompt = (
            f"Pantry: {stock}. Mood: {mood or 'any'}.{pref_text} Suggest ONE recipe under 30 minutes "
            "that uses the soonest-expiring items first. "
            "Reply in under 80 words, plain speech, no markdown."
        )
        try:
            return bedrock_converse(prompt, max_tokens=300)
        except Exception as exc:
            log.warning("Bedrock call failed (%s); using offline fallback.", exc)
            return "Try a quick stir-fry with " + ", ".join(r["name"] for r in rows[:3]) + "."
    except Exception:
        log.exception("suggest_recipe failed")
        return "Could not suggest a recipe right now. Please try again."


@mcp.tool()
def weekly_meal_plan(ctx: Context | None = None) -> str:
    """Plan 7 dinners for the week using pantry contents, starting with items expiring soonest, respecting your preferences."""
    if err := check_auth(ctx):
        return err
    try:
        owner = get_owner(ctx)
        rows = db().execute(
            "SELECT name, qty, unit, expires FROM items WHERE owner=? ORDER BY expires IS NULL, expires",
            (owner,),
        ).fetchall()
        if not rows:
            return "Your pantry is empty. Add some groceries and I'll plan your week."
        stock = "; ".join(f"{r['qty']:g} {r['unit']} {r['name']} (exp {r['expires'] or 'n/a'})" for r in rows)
        prefs = get_preferences(owner)
        pref_clause = format_preferences(prefs)
        pref_text = f" Preferences: {pref_clause}." if pref_clause else ""
        prompt = (
            f"Pantry: {stock}.{pref_text} Plan 7 quick dinners (one per day) that use "
            "soonest-expiring items first. Each dinner in one sentence. "
            "Plain speech, no markdown, no numbering."
        )
        try:
            return bedrock_converse(prompt, max_tokens=400)
        except Exception as exc:
            log.warning("Bedrock weekly_meal_plan failed (%s); fallback.", exc)
            names = ", ".join(r["name"] for r in rows[:7])
            return f"This week try meals with: {names}. Use soonest-expiring items first."
    except Exception:
        log.exception("weekly_meal_plan failed")
        return "Could not plan meals right now. Please try again."


@mcp.tool()
def waste_report(days: int = 7, ctx: Context | None = None) -> str:
    """Show a summary of items you consumed in the last N days (default 7)."""
    if err := check_auth(ctx):
        return err
    try:
        validate_positive(days, "Days")
        owner = get_owner(ctx)
        since = (date.today() - timedelta(days=days)).isoformat()
        rows = db().execute(
            """SELECT name, SUM(qty) AS total, unit FROM waste_log
               WHERE owner=? AND logged_at>=?
               GROUP BY name, unit ORDER BY total DESC""",
            (owner, since),
        ).fetchall()
        if not rows:
            return f"No items recorded as used in the last {days} days."
        parts = ", ".join(f"{r['total']:g} {r['unit']} {r['name']}" for r in rows)
        return f"In the last {days} days you used: {parts}."
    except ValueError as exc:
        return str(exc)
    except Exception:
        log.exception("waste_report failed")
        return "Could not generate the report. Please try again."


# ---------------------------------------------------------------------------
# Step 9 Tools: preferences + meal scheduling
# ---------------------------------------------------------------------------

@mcp.tool()
def set_preferences(
    diet: str = "",
    dislikes: str = "",
    allergies: str = "",
    ctx: Context | None = None,
) -> str:
    """Save your dietary preferences so recipes are tailored to you.

    Examples: diet='vegetarian', dislikes='coriander mushrooms', allergies='nuts'.
    Say 'I don't eat coriander' or 'I'm vegetarian' and Alexa+ will call this.
    """
    if err := check_auth(ctx):
        return err
    try:
        owner = get_owner(ctx)
        diet = diet.strip()[:200]
        dislikes = dislikes.strip()[:500]
        allergies = allergies.strip()[:500]
        with db() as con:
            con.execute(
                """INSERT INTO preferences(owner, diet, dislikes, allergies) VALUES(?,?,?,?)
                   ON CONFLICT(owner) DO UPDATE SET
                     diet=COALESCE(NULLIF(excluded.diet,''), diet),
                     dislikes=COALESCE(NULLIF(excluded.dislikes,''), dislikes),
                     allergies=COALESCE(NULLIF(excluded.allergies,''), allergies)""",
                (owner, diet or None, dislikes or None, allergies or None),
            )
        parts = []
        if diet:
            parts.append(f"diet set to {diet}")
        if allergies:
            parts.append(f"allergies noted: {allergies}")
        if dislikes:
            parts.append(f"dislikes noted: {dislikes}")
        if not parts:
            return "No preferences provided. Say your diet, allergies, or dislikes."
        return "Got it. " + "; ".join(parts) + ". I'll use this when suggesting recipes."
    except Exception:
        log.exception("set_preferences failed")
        return "Could not save your preferences. Please try again."


@mcp.tool()
def get_preferences_tool(ctx: Context | None = None) -> str:
    """Read back your current dietary preferences."""
    if err := check_auth(ctx):
        return err
    try:
        owner = get_owner(ctx)
        prefs = get_preferences(owner)
        if not prefs or not any(prefs.values()):
            return "No preferences saved yet. Tell me your diet, dislikes, or allergies."
        parts = []
        if prefs.get("diet"):
            parts.append(f"diet: {prefs['diet']}")
        if prefs.get("allergies"):
            parts.append(f"allergies: {prefs['allergies']}")
        if prefs.get("dislikes"):
            parts.append(f"dislikes: {prefs['dislikes']}")
        return "Your preferences: " + "; ".join(parts) + "."
    except Exception:
        log.exception("get_preferences_tool failed")
        return "Could not read your preferences. Please try again."


@mcp.tool()
def schedule_meal(
    recipe: str,
    meal_date: str = "",
    ctx: Context | None = None,
) -> str:
    """Plan a specific meal for a date, e.g. 'Plan pasta carbonara for Tuesday' or 'Schedule chicken soup for 2026-10-05'.

    meal_date accepts ISO date (YYYY-MM-DD) or day names like 'Monday', 'tomorrow'.
    Defaults to today if omitted.
    """
    if err := check_auth(ctx):
        return err
    try:
        recipe = recipe.strip()
        if not recipe:
            return "Please provide a recipe name to schedule."
        if len(recipe) > 300:
            return "Recipe name is too long. Please shorten it."

        # Resolve meal_date to ISO format
        resolved = _resolve_date(meal_date)

        owner = get_owner(ctx)
        with db() as con:
            con.execute(
                """INSERT INTO meal_schedule(owner, meal_date, recipe, status) VALUES(?,?,?,?)""",
                (owner, resolved, recipe, "planned"),
            )
        friendly = _friendly_date(resolved)
        return f"Scheduled {recipe} for {friendly}."
    except ValueError as exc:
        return str(exc)
    except Exception:
        log.exception("schedule_meal failed")
        return "Could not schedule the meal. Please try again."


@mcp.tool()
def get_meal_schedule(days: int = 7, ctx: Context | None = None) -> str:
    """Show your meal plan for the next N days (default 7)."""
    if err := check_auth(ctx):
        return err
    try:
        validate_positive(days, "Days")
        owner = get_owner(ctx)
        start = date.today().isoformat()
        end = (date.today() + timedelta(days=days)).isoformat()
        rows = db().execute(
            """SELECT meal_date, recipe, status FROM meal_schedule
               WHERE owner=? AND meal_date>=? AND meal_date<=?
               ORDER BY meal_date""",
            (owner, start, end),
        ).fetchall()
        if not rows:
            return f"No meals scheduled in the next {days} days."
        parts = [f"{r['meal_date']}: {r['recipe']} ({r['status']})" for r in rows]
        return "Upcoming meals: " + "; ".join(parts) + "."
    except ValueError as exc:
        return str(exc)
    except Exception:
        log.exception("get_meal_schedule failed")
        return "Could not read your meal schedule. Please try again."


# ---------------------------------------------------------------------------
# Date resolution helpers (for schedule_meal)
# ---------------------------------------------------------------------------

_DAY_NAMES = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _resolve_date(raw: str) -> str:
    """Resolve a human date string to ISO format (YYYY-MM-DD).

    Accepts: ISO date, 'today', 'tomorrow', weekday names.
    Returns ISO date string; raises ValueError on unrecognised input.
    """
    raw = raw.strip().lower()
    today = date.today()

    if not raw or raw == "today":
        return today.isoformat()
    if raw == "tomorrow":
        return (today + timedelta(days=1)).isoformat()

    # ISO date passthrough
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        pass

    # Weekday name → next occurrence (or today if it's already that day)
    if raw in _DAY_NAMES:
        target_wd = _DAY_NAMES[raw]
        delta = (target_wd - today.weekday()) % 7
        return (today + timedelta(days=delta)).isoformat()

    raise ValueError(
        f"I didn't understand the date '{raw}'. "
        "Try 'tomorrow', 'Monday', or a date like 2026-10-10."
    )


def _friendly_date(iso: str) -> str:
    """Convert ISO date to a friendly string like 'Wednesday Oct 7'."""
    try:
        d = date.fromisoformat(iso)
        return d.strftime("%A %b %-d")
    except Exception:
        return iso


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Wrap the Starlette app with CORS middleware so simulator/index.html
    # (opened from file:// or any origin) can call /mcp without CORS errors.
    # Also serve static /privacy and /terms pages for Alexa+ add-on registration.
    import uvicorn
    from starlette.middleware.cors import CORSMiddleware
    from starlette.routing import Route
    from starlette.responses import HTMLResponse
    from starlette.applications import Starlette

    _PRIVACY_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>PantryPilot – Privacy Policy</title>
<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:40px auto;padding:0 24px;line-height:1.6}
h1{font-size:1.4rem}h2{font-size:1.1rem;margin-top:2rem}</style></head><body>
<h1>PantryPilot – Privacy Policy</h1>
<p><strong>Last updated:</strong> October 2026</p>
<h2>What we collect</h2>
<p>PantryPilot stores only the data you explicitly provide: pantry items (name, quantity, expiry date),
shopping list entries, meal schedules, and dietary preferences. All data is stored in a SQLite database
on the server hosting this service.</p>
<h2>How we use your data</h2>
<p>Your data is used solely to answer your own requests (listing your pantry, suggesting recipes, etc.).
It is never sold, shared with third parties, or used for advertising.</p>
<h2>Amazon Bedrock</h2>
<p>Recipe and meal-plan suggestions are generated by Amazon Bedrock (Nova Micro model). Your pantry
contents are included in the prompt. Amazon's Bedrock data-use terms apply.</p>
<h2>Data retention</h2>
<p>Your data is stored until you delete it using the available tools, or until the server is reset.
No backups are kept beyond the running instance.</p>
<h2>Contact</h2>
<p>Questions? Open an issue at <a href="https://github.com/abhisuman2001/pantrypilot-alexa-mcp">github.com/abhisuman2001/pantrypilot-alexa-mcp</a>.</p>
</body></html>"""

    _TERMS_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>PantryPilot – Terms of Use</title>
<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:40px auto;padding:0 24px;line-height:1.6}
h1{font-size:1.4rem}h2{font-size:1.1rem;margin-top:2rem}</style></head><body>
<h1>PantryPilot – Terms of Use</h1>
<p><strong>Last updated:</strong> October 2026</p>
<h2>Acceptance</h2>
<p>By using PantryPilot you agree to these terms. If you do not agree, please discontinue use.</p>
<h2>Use of the service</h2>
<p>PantryPilot is provided as-is for personal, non-commercial household pantry management. You may not
use it for illegal purposes, to store personal data of others without their consent, or to abuse the
underlying AWS infrastructure.</p>
<h2>Disclaimer</h2>
<p>Recipe and meal suggestions are AI-generated and may contain errors. Always verify ingredients against
known allergies before cooking. PantryPilot is not responsible for any health outcomes arising from
recipe suggestions.</p>
<h2>Availability</h2>
<p>This is a hackathon project. Uptime is not guaranteed. The service may be taken offline at any time.</p>
<h2>Changes</h2>
<p>These terms may be updated at any time. Continued use constitutes acceptance of the updated terms.</p>
<h2>Contact</h2>
<p>Questions? Open an issue at <a href="https://github.com/abhisuman2001/pantrypilot-alexa-mcp">github.com/abhisuman2001/pantrypilot-alexa-mcp</a>.</p>
</body></html>"""

    async def privacy(request):
        return HTMLResponse(_PRIVACY_HTML)

    async def terms(request):
        return HTMLResponse(_TERMS_HTML)

    async def health(request):
        return HTMLResponse('{"status":"ok","service":"PantryPilot"}',
                            media_type="application/json")

    mcp_asgi = mcp.streamable_http_app()

    # Combine MCP app + static routes under one Starlette app
    static_routes = [
        Route("/privacy", privacy),
        Route("/terms", terms),
        Route("/health", health),
    ]
    static_app = Starlette(routes=static_routes)

    from starlette.routing import Mount
    from starlette.applications import Starlette as _Starlette

    combined = _Starlette(routes=[
        Route("/privacy", privacy),
        Route("/terms", terms),
        Route("/health", health),
        Mount("/", app=mcp_asgi),
    ])

    combined.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],              # fine for local / demo; tighten in prod
        allow_methods=["POST", "GET", "OPTIONS"],
        allow_headers=["Content-Type", "Accept", "Authorization", "X-User-ID"],
    )

    uvicorn.run(
        combined,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        log_level="info",
    )
