# PantryPilot: an Alexa+ MCP server that fights food waste

Track: **Alexa+** (self-hosted MCP server, Streamable HTTP) | Mini challenge: **AWS Builder** (Amazon Bedrock)

"Alexa, add six eggs that expire in ten days." "What's expiring soon?" "What can I cook tonight?"
PantryPilot gives Alexa+ a persistent household pantry plus Bedrock-generated recipes that use the soonest-expiring food first.

## Tools
`add_item`, `use_item`, `list_pantry`, `expiring_soon`, `suggest_recipe` (Bedrock Converse API; falls back offline).

## Run
```bash
pip install -r requirements.txt
python server.py            # http://localhost:8000/mcp
npx @modelcontextprotocol/inspector   # connect with Streamable HTTP to the URL above
```
For Bedrock: set AWS credentials, `AWS_REGION`, and `BEDROCK_MODEL_ID` (enable model access in the Bedrock console).
Deploy: `docker build -t pantrypilot .` then run on AWS App Runner/ECS behind HTTPS, and register the `/mcp` URL per the Alexa+ track docs.

## Submission checklist
- Devpost text, public repo (MIT license included), demo video under 3 min (lead with the voice demo).
- Product feedback per tool used; friction log (up to +10%); state that Bedrock is used in `suggest_recipe`.
- Repo private instead? Add the Amazon reviewers as collaborators at submission time.
