# ── Build stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── Runtime stage ────────────────────────────────────────────────────────────
FROM python:3.12-slim
WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code and static assets
COPY server.py .
COPY simulator/ ./simulator/

# Database lives in /data so it can be mounted as a volume in production.
# The PANTRY_DB env var overrides the default; we set a sensible container default here.
ENV PANTRY_DB=/data/pantry.db
ENV PORT=8000
ENV AWS_REGION=us-east-1

# Create the data directory (overridable via volume mount)
RUN mkdir -p /data

EXPOSE 8000

# Health-check: POST a tools/list to /mcp every 30 s
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "\
import urllib.request, json, sys; \
req=urllib.request.Request('http://localhost:8000/mcp', \
  data=json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/list'}).encode(), \
  headers={'Content-Type':'application/json','Accept':'application/json'}, \
  method='POST'); \
r=urllib.request.urlopen(req,timeout=4); sys.exit(0 if r.status==200 else 1)"

CMD ["python", "server.py"]
