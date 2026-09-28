# PantryPilot: an Alexa+ MCP server that fights food waste

Track: **Alexa+** (self-hosted MCP server, Streamable HTTP) | Mini challenge: **AWS Builder** (Amazon Bedrock)

> "Alexa, add six eggs that expire in ten days."  
> "What's expiring soon?"  
> "What can I cook tonight?"  
> "Plan my dinners for the week."

PantryPilot gives Alexa+ a persistent household pantry plus Bedrock-powered recipes that use the soonest-expiring food first — reducing food waste one voice command at a time.

---

## Tools (all voice-first: short, speakable, no markdown)

| Tool | What Alexa says |
|---|---|
| `add_item` | "Add 6 eggs that expire in 10 days" |
| `use_item` | "Use 2 eggs" |
| `list_pantry` | "What's in my pantry?" |
| `expiring_soon` | "What's expiring this week?" |
| `suggest_recipe` | "What can I cook tonight?" → Bedrock |
| `add_to_shopping_list` | "Add milk to my shopping list" |
| `get_shopping_list` | "Read my shopping list" |
| `weekly_meal_plan` | "Plan my dinners for the week" → Bedrock |
| `waste_report` | "What did I use this week?" |

---

## Quick start (local)

```bash
# 1. Clone and set up
git clone https://github.com/abhisuman2001/pantrypilot-alexa-mcp.git
cd pantrypilot-alexa-mcp

python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt

# 2. Configure AWS credentials (needed for Bedrock)
cp .env.example .env            # then fill in your values
# AWS_ACCESS_KEY_ID=...
# AWS_SECRET_ACCESS_KEY=...
# AWS_REGION=us-east-1
# BEDROCK_MODEL_ID=amazon.nova-micro-v1:0   (enable in Bedrock console first)
# MCP_API_KEY=                              # optional: leave blank for local dev

# 3. Run
.venv\Scripts\python server.py    # http://localhost:8000/mcp
```

The server starts with **CORS enabled** so the web simulator works out of the box.

---

## Web Simulator (demo asset)

Open `simulator/index.html` in a browser **while the server is running**:

```
file:///path/to/pantry-pilot/simulator/index.html
```

Features:
- Alexa-style voice chat UI (dark mode, animated ring)
- One-click quick actions for all 9 tools
- Forms for Add Item / Use Item / Add to Shopping List
- 🎤 Web Speech API voice input (Chrome/Edge)
- Natural language parsing: "add 6 eggs expire 10 days", "what can I cook?", etc.
- Settings panel: configure server URL + API key

---

## MCP Inspector

```bash
npx -y @modelcontextprotocol/inspector
# Connect with Streamable HTTP → http://localhost:8000/mcp
```

---

## Run tests

```bash
.venv\Scripts\pytest tests/ -v
# 64 passed
```

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8000` | HTTP port |
| `PANTRY_DB` | `pantry.db` | SQLite file path |
| `AWS_REGION` | `us-east-1` | Bedrock region |
| `BEDROCK_MODEL_ID` | `amazon.nova-micro-v1:0` | Model (enable in console) |
| `DEFAULT_OWNER` | `default` | Pantry owner when no header present |
| `MCP_API_KEY` | _(empty)_ | Bearer token; empty = auth disabled |

---

## Deploy (Docker → AWS App Runner)

```bash
# One-command deploy (builds, pushes to ECR, creates App Runner service):
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\deploy.ps1
```

**Live deployment:**
- Service URL: `https://kykh233phz.us-east-1.awsapprunner.com`
- MCP endpoint: `https://kykh233phz.us-east-1.awsapprunner.com/mcp`
- ECR image: `448049796441.dkr.ecr.us-east-1.amazonaws.com/pantrypilot:latest`

---

## Alexa+ Registration

```
TODO(verify): registration steps pending — see track Resources page.
The MCP endpoint to register is: https://<your-domain>/mcp
```

---

## Submission checklist

- [x] All 9 tools callable via MCP Inspector and web simulator
- [x] `pytest` passes (64 tests)
- [x] Bedrock called in `suggest_recipe` and `weekly_meal_plan` (with offline fallback)
- [x] Bearer-token auth (`MCP_API_KEY`), input validation, no stack traces exposed
- [x] Deployed over HTTPS — `https://kykh233phz.us-east-1.awsapprunner.com/mcp`
- [ ] Demo video recorded (≤ 3 min, voice demo first) — **record and link here**
- [x] Devpost description written — see [SUBMISSION.md](SUBMISSION.md)
- [x] Friction log completed (+10% bonus) — see [FRICTION_LOG.md](FRICTION_LOG.md) (7 items, severity + workaround + suggestion each)

