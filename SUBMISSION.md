# PantryPilot — Devpost Submission

## Tracks entered

- **Primary track:** Alexa+ (self-hosted MCP server, spec 2025-11-25, Streamable HTTP)
- **Mini challenge:** AWS Builder (Amazon Bedrock, AWS App Runner, Amazon ECR)

---

## What we built

**PantryPilot** is a self-hosted MCP server that gives Alexa+ a persistent household pantry and Bedrock-powered recipe suggestions — all by voice.

> "Alexa, add six eggs that expire in ten days."  
> "What's expiring soon?"  
> "What can I cook tonight?"  
> "Plan my dinners for the week."

PantryPilot fights food waste by always recommending recipes that use the soonest-expiring ingredients first. The server is deployed on AWS App Runner and reachable at:

**MCP endpoint:** `https://kykh233phz.us-east-1.awsapprunner.com/mcp`  
**Web simulator (no device needed):** `https://kykh233phz.us-east-1.awsapprunner.com/`

---

## How it works

| Layer | Technology |
|---|---|
| MCP server | `mcp` Python SDK 1.30 (FastMCP), Streamable HTTP transport at `/mcp`, protocol 2025-11-25 |
| Storage | SQLite (multi-user, scoped by `X-User-ID` header) |
| AI / recipes | Amazon Bedrock Converse API — `amazon.nova-micro-v1:0` |
| Deploy | Docker → Amazon ECR → AWS App Runner (HTTPS) |
| Auth | Optional bearer token (`MCP_API_KEY` env var) |
| Demo UI | `simulator/index.html` — Alexa-style voice chat, served by the server at `/` |

### The 13 tools

| Tool | What Alexa says | Uses Bedrock? |
|---|---|---|
| `add_item` | "Add 6 eggs expiring in 10 days" | No |
| `use_item` | "Use 2 eggs" (adds to shopping list when it runs out) | No |
| `list_pantry` | "What's in my pantry?" | No |
| `expiring_soon` | "What's expiring this week?" | No |
| `suggest_recipe` | "What can I cook tonight?" | **Yes** |
| `add_to_shopping_list` | "Add milk to my list" | No |
| `get_shopping_list` | "Read my shopping list" | No |
| `weekly_meal_plan` | "Plan my dinners for the week" | **Yes** |
| `waste_report` | "What did I use this week?" | No |
| `set_preferences` | "I'm vegetarian" / "I don't eat coriander" | No (feeds Bedrock prompts) |
| `get_preferences_tool` | "What are my dietary preferences?" | No |
| `schedule_meal` | "Plan pasta carbonara for Tuesday" | No |
| `get_meal_schedule` | "What's on my meal plan this week?" | No |

All responses are **voice-first**: short, speakable sentences — no markdown, no tables, no emojis. Bedrock calls have a 5 s connect / 15 s read timeout and capped `maxTokens` (300 for a recipe, 400 for a weekly plan); on any error the tool returns an offline suggestion built from the expiring items instead of failing.

---

## Challenges we ran into

1. **mcp SDK 2.x renamed `FastMCP`** — existing code fails to import on 2.x. Pinned `mcp<2`.
2. **CORS for the web simulator** — FastMCP has no CORS option; added Starlette `CORSMiddleware` to the app returned by `streamable_http_app()`.
3. **Adding extra routes broke `/mcp`** — wrapping `streamable_http_app()` in a second Starlette app skipped its lifespan, so every MCP call returned 500 (`Task group is not initialized`). Fixed by adding routes to the MCP app itself.
4. **Alexa+ registration** — no step-by-step docs found during the hackathon window. Marked `TODO(verify)` per the rules.
5. **SQLite on App Runner** — ephemeral filesystem means data resets on redeploy. The schema is recreated on startup; durable storage is future work.
6. **Windows PowerShell + AWS CLI JSON** — inline JSON args are mangled by PowerShell; solved by writing args to `.json` files.

Full details and suggestions in [FRICTION_LOG.md](FRICTION_LOG.md).

---

## What we learned

- FastMCP makes MCP server development fast once the SDK version is pinned correctly.
- Amazon Bedrock's Converse API is clean to use but requires manual model activation — a friction point worth flagging.
- Designing tools to be voice-first (short, speakable, no formatting) requires conscious discipline — it's easy to slip into markdown habits.
- AWS App Runner is a good fit for this use case: HTTPS by default, no load balancer or Nginx config required.

---

## What's next

- [ ] Complete Alexa+ registration once the process is documented
- [ ] Durable multi-household storage (e.g. Amazon Aurora Serverless or DynamoDB) instead of container-local SQLite
- [ ] Track items thrown out, not just used, so `waste_report` can report real waste
- [ ] Barcode-scan tool (camera → Bedrock vision → pantry entry)
- [ ] Expiry reminders via Alexa proactive notifications

---

## Built during the hackathon

Everything in this repo was built during the submission window (first commit 2026-09-28):

- MCP server with pantry, expiry, and recipe tools; multi-user isolation; shopping list (Steps 1–4)
- Bedrock `weekly_meal_plan`, `waste_report`, timeouts and fallbacks (Step 5)
- Auth + input validation, no stack traces in tool output (Step 6)
- Alexa-style web simulator with voice input (Step 7)
- Docker → ECR → App Runner deploy, privacy and terms pages (Step 8)
- Dietary preferences and meal scheduling, preference-aware Bedrock prompts (Step 9)
- 86 pytest tests, plus `scripts/live_smoke.py` to verify the live endpoint and Bedrock

**Repo:** https://github.com/abhisuman2001/pantrypilot-alexa-mcp  
**Live MCP:** https://kykh233phz.us-east-1.awsapprunner.com/mcp

---

## Product feedback (required)

Feedback on every tool, API, and SDK we used. AWS services used for the AWS Builder mini challenge are marked **[AWS]**.

### Alexa+ (MCP integration path)
- **Used for:** the target platform; PantryPilot is built as a self-hosted MCP server for Alexa+.
- **Worked well:** choosing open MCP over a proprietary skill SDK meant we could build and test everything locally with standard tools.
- **Needs work:** we found no documented way to register a self-hosted MCP endpoint with Alexa+ (endpoint URL, auth method, user identity header). We had to guess at how a user id would reach the server.
- **Onboarding:** easy to start, blocked at the last step (registration).
- **Would build again:** yes, once registration is documented.

### Amazon Bedrock — Converse API with Amazon Nova Micro [AWS]
- **Used for:** `suggest_recipe` and `weekly_meal_plan`; prompts list expiring items first plus the user's dietary preferences.
- **Worked well:** the Converse API is model-agnostic and the response shape is simple. Nova Micro is fast and cheap enough for voice latency.
- **Needs work:** models are not enabled by default, and `AccessDeniedException` does not link to the Model access page.
- **Onboarding:** about 15 minutes lost to model access; smooth after that.
- **Would build again:** yes.

### AWS App Runner [AWS]
- **Used for:** hosting the MCP server and simulator over HTTPS.
- **Worked well:** HTTPS URL out of the box, no load balancer or TLS setup, redeploy with `start-deployment`.
- **Needs work:** no persistent volume option, so SQLite data is lost on every redeploy.
- **Onboarding:** quick, apart from Windows CLI JSON quoting (below).
- **Would build again:** yes, for stateless services.

### Amazon ECR [AWS]
- **Used for:** storing the Docker image App Runner deploys.
- **Worked well:** `get-login-password` → `docker push` worked on the first try.
- **Needs work:** nothing significant.
- **Would build again:** yes.

### AWS CLI (Windows PowerShell)
- **Used for:** scripted deploy in `deploy.ps1`.
- **Needs work:** inline JSON arguments are mangled by PowerShell 5.1; we had to move every JSON argument into a `file://` JSON file.
- **Would build again:** yes, with JSON files from the start.

### MCP Python SDK (`mcp` 1.30, FastMCP)
- **Used for:** the server, tool registration from type hints and docstrings, and Streamable HTTP transport.
- **Worked well:** a tool is just a decorated function; `stateless_http=True` + `json_response=True` suits a voice backend.
- **Needs work:** 2.x renamed `FastMCP` (breaks existing code); there is no CORS option; `streamable_http_app()` is not in the API reference; mounting it inside another Starlette app silently skips its lifespan and turns every call into a 500.
- **Onboarding:** fast for the first tool, slower for anything beyond `mcp.run()`.
- **Would build again:** yes.

### MCP Inspector
- **Used for:** manually calling every tool over Streamable HTTP during development.
- **Worked well:** zero-install via `npx`, shows raw JSON-RPC.
- **Would build again:** yes.

---

## Friction log

See [FRICTION_LOG.md](FRICTION_LOG.md) — each entry has severity, what happened, workaround, and an actionable suggestion.
