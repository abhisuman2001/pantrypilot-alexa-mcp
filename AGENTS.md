# AGENTS.md: PantryPilot build guide for AI coding agents

Read this file fully before changing anything. It works for any agent IDE (Claude Code, Cursor, Copilot, Windsurf, Kiro, etc.).

## 1. Goal
Win the **Amazon Developer Hackathon**, **Alexa+ track** (plus the **AWS Builder** mini challenge), with a working, demo-ready product.
PantryPilot is a self-hosted **MCP server** (spec 2025-11-25 or later, **Streamable HTTP**) that lets Alexa+ manage a household pantry by voice and suggest recipes that use expiring food first, via **Amazon Bedrock**.

Example voice flows:
- "Add six eggs that expire in ten days" -> `add_item`
- "What's expiring soon?" -> `expiring_soon`
- "What can I cook tonight?" -> `suggest_recipe`

## 2. Hard requirements (do not break)
1. The repo must actually **import and run an MCP server** in code (`server.py`), not just mention it in the README.
2. Transport is **Streamable HTTP**, endpoint `/mcp`.
3. Bedrock must be **really called** in `suggest_recipe` (keep the offline fallback for demos).
4. Tool outputs are **voice-first**: short, speakable sentences, no markdown, no emojis, no tables.
5. Keep the repo open source (MIT `LICENSE` present) and self-contained: all instructions to run it live in `README.md`.
6. Do not invent Alexa+ registration steps or APIs. Verify against the track's Resources page and mark anything unverified as `TODO(verify)`.
7. Never commit secrets. Credentials come from environment variables.

## 3. Current state
| File | Purpose |
|---|---|
| `server.py` | FastMCP server, SQLite storage, 5 tools |
| `requirements.txt` | `mcp>=1.12`, `boto3` |
| `Dockerfile` | Container for App Runner/ECS |
| `README.md` | Human-facing run, deploy, and submission notes |
| `AGENTS.md` | This file |

Tools (contracts, keep names and signatures stable):
- `add_item(name, quantity=1, unit="pcs", days_until_expiry=None) -> str` upserts and adds quantity.
- `use_item(name, quantity=1) -> str` decrements or deletes.
- `list_pantry() -> str`
- `expiring_soon(days=3) -> str`
- `suggest_recipe(mood="") -> str` uses Bedrock Converse, falls back offline.

Env vars: `PORT` (8000), `PANTRY_DB` (pantry.db), `AWS_REGION` (us-east-1), `BEDROCK_MODEL_ID` (verify the model is enabled in the account).

Status: first version, **untested**. Step 1 below is to verify it.

## 4. Build plan (do in order, one step per commit)
1. **Verify baseline.** `pip install -r requirements.txt && python server.py`, then connect the MCP Inspector (`npx @modelcontextprotocol/inspector`) to `http://localhost:8000/mcp`. Fix any SDK-version issues (e.g. FastMCP constructor args). Exit: all 5 tools callable.
2. **Tests.** Add `tests/test_tools.py` (pytest) using a temp `PANTRY_DB`; cover add/upsert, use to zero, expiry ordering, empty pantry, Bedrock failure fallback (mock boto3). Exit: `pytest` green.
3. **Multi-user.** Add an `owner` column and scope every query by user. Take the user id from request context/headers if Alexa+ provides one (`TODO(verify)`), else a configurable default. Optional: Supabase/Postgres via `DATABASE_URL`, with SQLite still the default.
4. **Shopping list.** Add `add_to_shopping_list`, `get_shopping_list`; `use_item` auto-suggests adding an item when it runs out. Keep replies to one or two sentences.
5. **Smarter Bedrock.** Add `weekly_meal_plan` and `waste_report` (items used vs thrown out). Keep prompts short, cap `maxTokens`, set timeouts, and return a fallback on any error.
6. **Auth and safety.** Add a bearer-token check via env `MCP_API_KEY`, input validation (name length, quantity > 0), and no stack traces in tool output.
7. **Web simulator (demo asset).** A single-page `simulator/index.html` that mimics an Alexa+ voice chat and calls the MCP server, for the video and as the fallback path judges can run without a device. Include its source in the repo.
8. **Deploy.** Build the Docker image, deploy to AWS App Runner or ECS behind HTTPS, and record the URL in `README.md`. Register with Alexa+ per the track docs.
9. **Submission pack.** Fill the README checklist: demo video under 3 min (voice demo first), Devpost description, per-tool product feedback (say Bedrock is used in `suggest_recipe`), friction log with severity, workaround, and suggestion (up to +10% judging bonus), and an explanation of what was built in the submission window.

## 5. Conventions
- Python 3.12, type hints, small functions, no new dependencies unless needed (justify in the commit message).
- Every tool needs a clear docstring: it is the description Alexa+ reads to pick the tool.
- Return `str`, never raise to the client; catch, log, and return a friendly sentence.
- Keep `server.py` readable; split into modules only past ~300 lines.

## 6. Definition of done
- [ ] All tools work through the MCP Inspector and via the simulator
- [ ] `pytest` passes
- [ ] Bedrock call verified with real credentials
- [ ] Deployed over HTTPS, `/mcp` reachable
- [ ] README complete, demo video recorded, friction log written
