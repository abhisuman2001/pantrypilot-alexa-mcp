# PantryPilot – Friction Log

> Amazon Developer Hackathon bonus artifact.
> Format: **Severity** | **What happened** | **Workaround used** | **Suggestion for Amazon / MCP SDK team**

---

## FR-01 — mcp SDK 2.x silent breaking change
**Severity:** High  
**Phase:** Step 1 (baseline)

**What happened:**  
`mcp>=2` renamed `FastMCP` to `MCPServer` and changed the `run()` API. Installing the latest SDK caused `ImportError: cannot import name 'FastMCP'` with no migration guide in the changelog.

**Workaround:**  
Pinned `mcp<2` in `requirements.txt` (version 1.30.0). All tools and tests pass.

**Suggestion:**  
Publish a migration guide at the top of the 2.x changelog. Consider keeping `FastMCP` as a deprecated alias for one major version.

---

## FR-02 — No built-in CORS support in FastMCP Streamable HTTP
**Severity:** High  
**Phase:** Step 7 (web simulator)

**What happened:**  
`simulator/index.html` opened from `file://` triggers browser CORS blocking when calling `http://localhost:8000/mcp`. FastMCP's `run()` method does not expose a CORS configuration option.

**Workaround:**  
Used the internal `mcp.streamable_http_app()` method to get the underlying Starlette ASGI app, then added `starlette.middleware.cors.CORSMiddleware` before calling `uvicorn.run()`. This is not documented.

**Suggestion:**  
Add a `cors_origins` parameter to `FastMCP.__init__()` or `FastMCP.run()`. Example: `mcp = FastMCP("MyServer", cors_origins=["*"])`.

---

## FR-03 — Alexa+ MCP registration process undocumented
**Severity:** High  
**Phase:** Step 8 (deploy / registration)

**What happened:**  
The Alexa+ track Resources page does not describe the exact steps to register a self-hosted MCP server endpoint with Alexa+. No API, no SDK method, no console UI flow was found during the hackathon window.

**Workaround:**  
Marked all registration steps as `TODO(verify)` in README and AGENTS.md. The MCP server is deployed and reachable over HTTPS; registration can be completed once documentation is available.

**Suggestion:**  
Publish a step-by-step "Register an MCP server with Alexa+" guide as part of the hackathon Resources page, including which fields are required (endpoint URL, auth method, tool manifest format).

---

## FR-04 — SQLite data lost on App Runner restart / redeploy
**Severity:** Medium  
**Phase:** Step 8 (deploy)

**What happened:**  
AWS App Runner uses an ephemeral container filesystem. Setting `PANTRY_DB=/tmp/pantry.db` means the pantry is wiped on every redeploy or container restart, making the live demo unreliable for multi-session testing.

**Workaround:**  
Designed the schema to be re-created on startup (`CREATE TABLE IF NOT EXISTS`). For the demo, items are re-added before recording. The code already supports `DATABASE_URL` for Postgres (Step 3 multi-user path).

**Suggestion:**  
App Runner should support EFS mounts or a managed SQLite-compatible key-value store for lightweight persistent state. Alternatively, document the recommended pattern for ephemeral-container apps that need durable local storage.

---

## FR-05 — Bedrock model access requires manual console step
**Severity:** Medium  
**Phase:** Step 1 / Step 5 (Bedrock)

**What happened:**  
`amazon.nova-micro-v1:0` is not enabled by default. The first call returns `AccessDeniedException: You don't have access to the model`. The error message does not include a link or instructions.

**Workaround:**  
Navigated to **Bedrock console → Model access → Request access** and enabled Nova Micro manually. Added a note in README.

**Suggestion:**  
Include a direct deep-link in the `AccessDeniedException` error message: `https://console.aws.amazon.com/bedrock/home#/modelaccess`. This alone would save 10–15 minutes for every new Bedrock developer.

---

## FR-06 — Windows PowerShell JSON escaping breaks AWS CLI inline args
**Severity:** Low  
**Phase:** Step 8 (deploy scripting)

**What happened:**  
AWS CLI `--instance-configuration '{"Cpu":"0.25 vCPU",...}'` works on Linux/macOS but PowerShell strips inner quotes, producing `{Cpu:0.25 vCPU,...}` which the CLI rejects as `Invalid JSON`.

**Workaround:**  
Saved all JSON args to temporary `.json` files and passed them with `file://` prefix. Added `trust-policy.json`, `apprunner-source.json`, `apprunner-instance.json` to the repo.

**Suggestion:**  
The AWS CLI Windows installer should ship a PowerShell module that handles argument escaping automatically, or the CLI should detect and fix single-quoted JSON on Windows.

---

## FR-07 — `streamable_http_app()` not in public FastMCP docs
**Severity:** Low  
**Phase:** Step 7 (CORS fix)

**What happened:**  
The method needed to get the ASGI app (`mcp.streamable_http_app()`) is not documented in the public API reference. Found it by introspecting `dir(FastMCP)` at runtime.

**Workaround:**  
Added a runtime check: `[m for m in dir(FastMCP) if 'app' in m.lower()]` confirmed the method exists in 1.30.0.

**Suggestion:**  
Document all public ASGI/transport methods in the FastMCP API reference, including the return type (Starlette `Application`) and whether they are stable.

---

*Total friction items: 7 | High: 3 | Medium: 2 | Low: 2*
