# mintsoft-mcp

A [Model Context Protocol](https://modelcontextprotocol.io) server for the
[Mintsoft](https://www.mintsoft.com/) warehouse management system, so Claude and any
other MCP-capable LLM client can look up orders, products, stock, inbound, returns and
reports — and, if you deliberately enable it, make guarded changes.

> **Unofficial.** Not affiliated with or endorsed by Mintsoft / Access Group.

## Why the safety model matters

**Mintsoft has no sandbox — every API key points at a live WMS.** This server is built
around that fact:

- **Read-only by default.** 37 read tools (GETs only). The 8 write tools are not
  registered at all unless `MINTSOFT_ENABLE_WRITES=1` — when off, there is zero write
  surface for a model to misuse.
- **Client allowlist.** Even with writes on, every write is refused unless the target
  record belongs to a ClientId in `MINTSOFT_WRITE_CLIENT_ALLOWLIST` (default: empty =
  refuse everything). Point it at a dedicated test client account, not your real brands.
- **Destructive ops double-gated.** `delete_product` also requires `confirm=True`;
  `amend_bundle` refuses if the bundle has alt codes or recent stock activity unless
  `force=True`, and rolls back if the recreate fails.
- **Everything audit-logged.** Every API call (reads included) is appended to a local
  JSONL audit log (`~/.mintsoft-mcp/mintsoft_api_audit.jsonl`) — timestamp, method,
  path, body, status, affected id. The API key is never logged. Readable in-session via
  the `get_api_audit_log` tool for tracing or recovering from changes.

## Install

From source (PyPI release coming):

```sh
uv tool install git+https://github.com/AhamadFalah/mintsoft-mcp
# or: pip install git+https://github.com/AhamadFalah/mintsoft-mcp
```

You need a Mintsoft API key (Mintsoft → Settings → API).

## Hook it up

**Claude Code**

```sh
claude mcp add mintsoft -e MINTSOFT_API_KEY=your-key -- mintsoft-mcp
```

**Claude Desktop / Cursor / Windsurf / VS Code** — add to the client's MCP config
(`claude_desktop_config.json`, `.cursor/mcp.json`, etc.):

```json
{
  "mcpServers": {
    "mintsoft": {
      "command": "mintsoft-mcp",
      "env": { "MINTSOFT_API_KEY": "your-key" }
    }
  }
}
```

To enable writes (against a test client only — see safety model above):

```json
"env": {
  "MINTSOFT_API_KEY": "your-key",
  "MINTSOFT_ENABLE_WRITES": "1",
  "MINTSOFT_WRITE_CLIENT_ALLOWLIST": "3"
}
```

## Tools

### Reads (37, always on)

| Area | Tools |
|---|---|
| Orders | `get_order`, `search_orders`, `get_order_id`, `list_orders`, `list_order_statuses`, `get_order_items`, `get_order_comments`, `track_order_by_tracking_number`, `list_order_channels` |
| Products & stock | `get_product`, `lookup_product_id`, `search_products`, `list_products`, `get_alt_codes`, `get_bundle`, `get_product_inventory`, `get_stock_levels_by_warehouse`, `get_stock_levels_updated_since`, `get_stock_flow`, `list_suppliers`, `precheck_discontinue` |
| Inbound & returns | `list_asns`, `get_asn`, `list_returns`, `get_return` |
| Warehouse | `list_warehouses`, `list_locations`, `list_warehouse_zones` |
| Account & reference | `list_clients`, `list_couriers`, `list_invoices`, `get_invoice`, `list_order_rules`, `run_report`, `get_ref_data` |
| Meta | `mintsoft_get` (generic read-only GET passthrough for any endpoint not wrapped), `get_api_audit_log` |

### Writes (8, gated)

`add_order_comment`, `add_order_tag`, `create_product`, `update_product`,
`add_alt_code`, `delete_product` (needs `confirm=True`), `amend_bundle`,
`discontinue_sku` (footprint pre-check → rename `[Discontinued] …` + DisCont flag,
same ProductId so history survives).

## Baked-in Mintsoft API quirks

Things this server handles that the official docs don't tell you:

- **Cloudflare blocks default user-agents** (error 1010) — a browser UA is sent.
- **Validation failures come back HTTP 200 with `Success: false`** — surfaced as
  errors, not silently swallowed.
- **`Product.CommodityCode` must be a nested object** `{"Code": "..."}`; a plain
  string is silently dropped. The client coerces strings automatically.
- **`AltCodes` POST takes query params, not a JSON body**, and `ConnectMappingType`
  is a channel *name string* (`"Amazon"`, `"Shopify"`, `"API"`…) despite Swagger
  saying integer.
- **Bundles are hidden from `Product/Search`** — find them via `Product/List` or by id.
- **There is no in-place bundle edit** — `amend_bundle` does delete + recreate (new
  ProductId) with guards and rollback.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `MINTSOFT_API_KEY` | — | **Required.** Your Mintsoft API key. |
| `MINTSOFT_ENABLE_WRITES` | `0` | Register the write tools. |
| `MINTSOFT_WRITE_CLIENT_ALLOWLIST` | *(empty)* | Comma-separated ClientIds writes may touch. Empty = all writes refused. |
| `MINTSOFT_AUDIT` | `1` | Audit-log every API call. |
| `MINTSOFT_AUDIT_WRITES_ONLY` | `0` | Log only PUT/POST/DELETE. |
| `MINTSOFT_AUDIT_LOG` | `~/.mintsoft-mcp/mintsoft_api_audit.jsonl` | Audit log path. |
| `MINTSOFT_SECRET_PROJECT` / `MINTSOFT_SECRET_NAME` | *(unset)* | Optional: fetch the key from GCP Secret Manager via `gcloud` instead. |

## Smoke test

`scripts/smoke_test.py` verifies the full 187-endpoint surface: read-only tiers run
live; the guarded `--write` pass runs a create→read→delete cycle **only** against a
client whose name contains "test" (or `MINTSOFT_TEST_CLIENT_ID`) and skips writes
entirely if none exists.

```sh
python scripts/smoke_test.py            # read-only
python scripts/smoke_test.py --write    # + guarded write demo
```

## Licence

[Apache-2.0](LICENSE).

<!-- MCP registry ownership marker -->
mcp-name: io.github.ahamadfalah/mintsoft-mcp
