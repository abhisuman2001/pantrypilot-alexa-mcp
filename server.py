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
    """Suggest one quick recipe that uses expiring items first (powered by Amazon Bedrock)."""
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
        prompt = (
            f"Pantry: {stock}. Mood: {mood or 'any'}. Suggest ONE recipe under 30 minutes "
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
    """Plan 7 dinners for the week using pantry contents, starting with items expiring soonest."""
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
        prompt = (
            f"Pantry: {stock}. Plan 7 quick dinners (one per day) that use "
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
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Wrap the Starlette app with CORS middleware so simulator/index.html
    # (opened from file:// or any origin) can call /mcp without CORS errors.
    import uvicorn
    from starlette.middleware.cors import CORSMiddleware

    asgi = mcp.streamable_http_app()
    asgi.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],              # fine for local / demo; tighten in prod
        allow_methods=["POST", "OPTIONS"],
        allow_headers=["Content-Type", "Accept", "Authorization", "X-User-ID"],
    )
    uvicorn.run(
        asgi,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        log_level="info",
    )
