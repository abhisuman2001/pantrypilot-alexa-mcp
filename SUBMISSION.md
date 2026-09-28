# PantryPilot — Devpost Submission

## What we built

**PantryPilot** is a self-hosted MCP server that gives Alexa+ a persistent household pantry and Bedrock-powered recipe suggestions — all by voice.

> "Alexa, add six eggs that expire in ten days."  
> "What's expiring soon?"  
> "What can I cook tonight?"  
> "Plan my dinners for the week."

PantryPilot fights food waste by always recommending recipes that use the soonest-expiring ingredients first. The server is deployed on AWS App Runner and reachable at:

**MCP endpoint:** `https://kykh233phz.us-east-1.awsapprunner.com/mcp`

---

## How it works

| Layer | Technology |
|---|---|
| MCP server | FastMCP (Python), Streamable HTTP transport |
| Storage | SQLite (multi-user, scoped by `X-User-ID` header) |
| AI / recipes | Amazon Bedrock Converse API — `amazon.nova-micro-v1:0` |
| Deploy | Docker → Amazon ECR → AWS App Runner (HTTPS, auto-scaled) |
| Auth | Optional bearer-token (`MCP_API_KEY` env var) |
| Demo UI | `simulator/index.html` — Alexa-style voice chat, no device needed |

### The 9 tools

| Tool | What Alexa says | Uses Bedrock? |
|---|---|---|
| `add_item` | "Add 6 eggs expiring in 10 days" | No |
| `use_item` | "Use 2 eggs" | No |
| `list_pantry` | "What's in my pantry?" | No |
| `expiring_soon` | "What's expiring this week?" | No |
| `suggest_recipe` | "What can I cook tonight?" | **Yes** |
| `add_to_shopping_list` | "Add milk to my list" | No |
| `get_shopping_list` | "Read my shopping list" | No |
| `weekly_meal_plan` | "Plan my dinners for the week" | **Yes** |
| `waste_report` | "What did I use this week?" | No |

All responses are **voice-first**: short, speakable sentences — no markdown, no tables, no emojis.

---

## Challenges we ran into

1. **mcp SDK 2.x broke `FastMCP`** — renamed without a migration guide. Pinned `mcp<2`.
2. **CORS for the web simulator** — FastMCP has no built-in CORS config; had to access the internal Starlette app via `streamable_http_app()`.
3. **Alexa+ registration** — no step-by-step docs found during the hackathon window. Marked `TODO(verify)` per the rules.
4. **SQLite on App Runner** — ephemeral filesystem means data resets on redeploy. Mitigated with auto-recreating schema; Postgres path ready via `DATABASE_URL`.
5. **Windows PowerShell + AWS CLI JSON** — inline JSON args are mangled by PowerShell; solved by writing args to temp `.json` files.

Full details and suggestions in [FRICTION_LOG.md](FRICTION_LOG.md).

---

## What we learned

- FastMCP makes MCP server development remarkably fast once the SDK version is pinned correctly.
- Amazon Bedrock's Converse API is clean to use but requires manual model activation — a friction point worth flagging.
- Designing tools to be voice-first (short, speakable, no formatting) requires conscious discipline — it's easy to slip into markdown habits.
- AWS App Runner is excellent for this use case: HTTPS by default, scales to zero, no Nginx config required.

---

## What's next

- [ ] Complete Alexa+ registration once the process is documented
- [ ] Migrate SQLite → Amazon Aurora Serverless for durable multi-household storage
- [ ] Add a barcode-scan tool (Alexa camera → Bedrock vision → pantry entry)
- [ ] Expiry push notifications via Alexa proactive events

---

## Built during the hackathon

- MCP server with 9 tools (Steps 1–5)
- Multi-user isolation, shopping list, Bedrock meal planning, waste reporting
- Auth + safety layer (Step 6)
- Alexa-style web simulator with voice input (Step 7)
- Docker → ECR → App Runner deploy with CORS (Step 8)
- 64 pytest tests, live endpoint verified with Bedrock

**Repo:** https://github.com/abhisuman2001/pantrypilot-alexa-mcp  
**Live MCP:** https://kykh233phz.us-east-1.awsapprunner.com/mcp

---

## Per-tool product feedback (required)

| Tool | Feedback |
|---|---|
| `add_item` | Works well for voice. Upsert (accumulating quantity) is the right default for repeat buys. |
| `use_item` | Auto-adding to shopping list when stock hits zero is a killer feature for Alexa+ routines. |
| `list_pantry` | Alexa should offer to read the full list or just the count, depending on pantry size. |
| `expiring_soon` | Most-used tool in testing. Ordering by soonest-first is critical for food-waste use case. |
| `suggest_recipe` | **Uses Amazon Bedrock `amazon.nova-micro-v1:0`** via the Converse API. Mood parameter lets users say "something quick" or "Italian". Offline fallback ensures demo reliability. |
| `add_to_shopping_list` | Natural follow-on after `use_item`. Deduplication (upsert) works well. |
| `get_shopping_list` | Simple but essential. Next version: read list aloud by category. |
| `weekly_meal_plan` | **Uses Amazon Bedrock** — generates 7-day dinner plan from pantry contents. Capped at 200 tokens for voice-friendly length. |
| `waste_report` | Tracks what was used vs. what expired. Useful for habit-building ("you wasted 3 items last week"). |
