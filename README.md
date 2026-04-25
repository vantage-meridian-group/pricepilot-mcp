# PricePilot MCP Server

Free Amazon pricing intelligence for multi-channel CPG brands, exposed as a Model Context Protocol server. Six read-only tools over weekly Buy Box scans across Grocery, Health & Beauty, Household, and Pet Supplies.

A free alternative to NielsenIQ / SPINS syndicated category data — accessible from any MCP client (Claude Desktop, Claude.ai, Cursor, Continue, agent frameworks).

## What it does

Given a price and a category, the server answers questions like:

- Where does this Amazon price sit against 100+ tracked competitors?
- Is the category trending up, stable, or falling over the last 30 days?
- What are the budget / midmarket / premium price bands?
- How do these N SKUs stack against each other on the shelf?

The server returns derived statistics only — percentile rank, Price Index, trend direction, tier bands. No raw competitor prices are exposed.

## Connect

### Hosted endpoint (no install)

```json
{
  "mcpServers": {
    "pricepilot": {
      "url": "https://pricepilot-mcp.onrender.com/mcp"
    }
  }
}
```

No API key. Rate limited to 60 requests/minute, 1000/day.

### Claude Desktop (one-click, recommended)

Use the official `.mcpb` extension at https://github.com/vantage-meridian-group/pricepilot-mcpb/releases/latest — drag-and-drop into Claude Desktop.

### Local stdio (for development)

```bash
pip install -e .
DATABASE_URL=postgresql://... python -m pricepilot_mcp
```

## Tools

| Tool | What it returns |
|---|---|
| `get_price_position` | Percentile rank, Price Index, position (Value / Parity / Premium) for a single price |
| `get_category_trend` | 30-day trend direction (Rising / Stable / Falling) with sample size |
| `get_category_overview` | Tier breakdown (budget / midmarket / premium bands) and median |
| `compare_products` | Per-SKU stack rank for a list of products against the category |
| `list_categories` | Available categories with product counts and trend — call this first |
| `server_status` | Health and data freshness (degraded if seed is >10 days old) |

All tools are `readOnlyHint=true`, `destructiveHint=false`, `openWorldHint=false`.

## Categories

Refreshed weekly from Amazon Buy Box scans:

- Grocery & Gourmet Food
- Health & Beauty
- Household
- Pet Supplies

## Architecture

- FastMCP with Streamable HTTP and stdio transports
- SQLAlchemy + PostgreSQL for benchmark snapshots (read-only from the server's perspective)
- Rate-limited per-consumer with daily and per-minute buckets
- Dockerfile included; deploys to any container host (production runs on Render)

## Run with Docker

```bash
docker build -t pricepilot-mcp .
docker run -p 8081:8081 -e DATABASE_URL=postgresql://... pricepilot-mcp
```

## Operator note

The MCP server is the free, category-level surface of PricePilot. Per-SKU pricing recommendations (R1 price-alignment is live; R2 / R3 / R5 expanding through Q2) are delivered via the paid platform at https://app.pricepilot.vantagemeridiangroup.com.

## License

MIT — see [LICENSE](LICENSE).
