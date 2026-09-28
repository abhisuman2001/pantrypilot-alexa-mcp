"""PantryPilot - an Alexa+ MCP server (Streamable HTTP) that fights food waste.
Tools are voice-first: short, speakable answers. Recipes come from Amazon Bedrock.

Step 3: Multi-user support scoped by 'owner' (X-User-ID header or DEFAULT_OWNER).
  - TODO(verify): confirm the exact header/claim Alexa+ uses for user identity.
Step 4: Shopping list. add_to_shopping_list / get_shopping_list tools added.
  - use_item auto-adds an item to the shopping list when it runs out.
  - add_item removes the item from the shopping list when restocked.
"""
import os
import logging
import sqlite3
from datetime import date, timedelta

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
MODEL = os.getenv("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0")
DEFAULT_OWNER = os.getenv("DEFAULT_OWNER", "default")


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
    owner = get_owner(ctx)
    name = name.strip().lower()
    exp = (
        (date.today() + timedelta(days=days_until_expiry)).isoformat()
        if days_until_expiry is not None
        else None
    )
    with db() as con:
        con.execute(
            """INSERT INTO items(owner, name, qty, unit, expires) VALUES(?,?,?,?,?)
               ON CONFLICT(owner, name) DO UPDATE SET
                 qty=qty+excluded.qty,
                 expires=COALESCE(excluded.expires, expires)""",
            (owner, name, quantity, unit, exp),
        )
        # Remove from shopping list now that the item is restocked.
        removed = con.execute(
            "DELETE FROM shopping_list WHERE owner=? AND name=?", (owner, name)
        ).rowcount
    suffix = " Removed from your shopping list." if removed else ""
    return f"Added {quantity:g} {unit} of {name}.{suffix}"



@mcp.tool()
def use_item(
    name: str,
    quantity: float = 1,
    ctx: Context | None = None,
) -> str:
    """Record that some of an item was used up or thrown out."""
    owner = get_owner(ctx)
    name = name.strip().lower()
    with db() as con:
        row = con.execute(
            "SELECT qty, unit FROM items WHERE owner=? AND name=?", (owner, name)
        ).fetchone()
        if not row:
            return f"You don't have any {name}."
        left = row["qty"] - quantity
        if left <= 0:
            unit = row["unit"]
            con.execute(
                "DELETE FROM items WHERE owner=? AND name=?", (owner, name)
            )
            # Auto-add to shopping list so the user remembers to restock.
            con.execute(
                """INSERT INTO shopping_list(owner, name, qty, unit) VALUES(?,?,?,?)
                   ON CONFLICT(owner, name) DO UPDATE SET qty=excluded.qty, unit=excluded.unit""",
                (owner, name, 1, unit),
            )
            return f"That was the last of your {name}. I've added it to your shopping list."
        con.execute(
            "UPDATE items SET qty=? WHERE owner=? AND name=?", (left, owner, name)
        )
    return f"{left:g} {row['unit']} of {name} left."


@mcp.tool()
def add_to_shopping_list(
    name: str,
    quantity: float = 1,
    unit: str = "pcs",
    ctx: Context | None = None,
) -> str:
    """Add an item to the shopping list, e.g. 'add milk to my shopping list'."""
    owner = get_owner(ctx)
    name = name.strip().lower()
    with db() as con:
        con.execute(
            """INSERT INTO shopping_list(owner, name, qty, unit) VALUES(?,?,?,?)
               ON CONFLICT(owner, name) DO UPDATE SET qty=excluded.qty, unit=excluded.unit""",
            (owner, name, quantity, unit),
        )
    return f"Added {quantity:g} {unit} of {name} to your shopping list."


@mcp.tool()
def get_shopping_list(ctx: Context | None = None) -> str:
    """Read out everything on the shopping list."""
    owner = get_owner(ctx)
    rows = db().execute(
        "SELECT name, qty, unit FROM shopping_list WHERE owner=? ORDER BY name", (owner,)
    ).fetchall()
    if not rows:
        return "Your shopping list is empty."
    return "Shopping list: " + ", ".join(
        f"{r['qty']:g} {r['unit']} {r['name']}" for r in rows
    ) + "."


@mcp.tool()
def list_pantry(ctx: Context | None = None) -> str:
    """Read out everything currently in the pantry."""
    owner = get_owner(ctx)
    rows = db().execute(
        "SELECT * FROM items WHERE owner=? ORDER BY name", (owner,)
    ).fetchall()
    if not rows:
        return "Your pantry is empty."
    return "You have " + ", ".join(
        f"{r['qty']:g} {r['unit']} {r['name']}" for r in rows
    ) + "."


@mcp.tool()
def expiring_soon(days: int = 3, ctx: Context | None = None) -> str:
    """List items that expire within the next N days (default 3)."""
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


@mcp.tool()
def suggest_recipe(mood: str = "", ctx: Context | None = None) -> str:  # noqa: E501
    """Suggest one quick recipe that uses expiring items first (powered by Amazon Bedrock)."""
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
        import boto3
        resp = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_REGION", "us-east-1"),
        ).converse(
            modelId=MODEL,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 300},
        )
        return resp["output"]["message"]["content"][0]["text"].strip()
    except Exception as exc:
        log.warning("Bedrock call failed (%s); using offline fallback.", exc)
        return "Try a quick stir-fry with " + ", ".join(row["name"] for row in rows[:3]) + "."


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="streamable-http")  # endpoint: http://0.0.0.0:8000/mcp
