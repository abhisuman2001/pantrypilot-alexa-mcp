"""Live smoke-test for the PantryPilot App Runner deployment.
Calls tools/list, add_item, list_pantry, expiring_soon, and suggest_recipe (Bedrock).
Usage: python test_live.py [url]
"""
import urllib.request, json, sys, time

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://kykh233phz.us-east-1.awsapprunner.com"
MCP  = f"{BASE}/mcp"
PASS = 0; FAIL = 0


def call(method, name="", args=None):
    body = json.dumps({
        "jsonrpc": "2.0", "id": int(time.time() * 1000),
        "method": method,
        "params": {"name": name, "arguments": args or {}} if name else {}
    }).encode()
    req = urllib.request.Request(MCP, data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def check(label, response, expect=""):
    global PASS, FAIL
    text = ""
    if "result" in response:
        content = response["result"].get("content", [])
        text = content[0]["text"] if content else str(response["result"])
    elif "error" in response:
        text = f"ERROR: {response['error']}"
    ok = (not expect) or (expect.lower() in text.lower())
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {label}: {text[:100]}")
    if ok: PASS += 1
    else:  FAIL += 1
    return text


print(f"\n=== PantryPilot Live Test ===\nEndpoint: {MCP}\n")

# 1. tools/list
print("[1] tools/list")
r = call("tools/list")
tools = [t["name"] for t in r.get("result", {}).get("tools", [])]
ok = len(tools) == 9
print(f"  [{'PASS' if ok else 'FAIL'}] Got {len(tools)} tools: {', '.join(tools)}")
PASS += ok; FAIL += (not ok)

# 2. add_item
print("\n[2] add_item (live-test-apple, 3 pcs, 5 days)")
r = call("tools/call", "add_item", {"name": "live-test-apple", "quantity": 3, "unit": "pcs", "days_until_expiry": 5})
check("add_item", r, "live-test-apple")

# 3. list_pantry
print("\n[3] list_pantry")
r = call("tools/call", "list_pantry")
check("list_pantry", r, "live-test-apple")

# 4. expiring_soon
print("\n[4] expiring_soon (days=7)")
r = call("tools/call", "expiring_soon", {"days": 7})
check("expiring_soon", r, "live-test-apple")

# 5. suggest_recipe (Bedrock)
print("\n[5] suggest_recipe -- tests Bedrock Converse API")
r = call("tools/call", "suggest_recipe", {"mood": "quick dinner"})
text = check("suggest_recipe", r)
bedrock_used = "fallback" not in text.lower() and "sorry" not in text.lower()
print(f"  [{'PASS' if bedrock_used else 'WARN'}] Bedrock {'called successfully' if bedrock_used else 'fell back to offline mode'}")

# 6. use_item (cleanup)
print("\n[6] use_item (cleanup live-test-apple)")
r = call("tools/call", "use_item", {"name": "live-test-apple", "quantity": 3})
check("use_item", r)

print(f"\n=== Results: {PASS} passed, {FAIL} failed ===\n")
sys.exit(0 if FAIL == 0 else 1)
