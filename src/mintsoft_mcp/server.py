"""Mintsoft MCP server (FastMCP / stdio).

Safe-by-default — Mintsoft has NO sandbox environment; every account is live:
  * READ tools are always registered (GETs only).
  * WRITE tools register ONLY when env MINTSOFT_ENABLE_WRITES is truthy. When off,
    they don't exist — zero write surface.
  * Even when on, writes are scoped to an allowlist of ClientIds
    (env MINTSOFT_WRITE_CLIENT_ALLOWLIST, default EMPTY = all writes refused), every
    response's `Success` flag is checked, and delete_* needs confirm=True.

Run:  mintsoft-mcp                                  # read-only (default)
      MINTSOFT_ENABLE_WRITES=1 mintsoft-mcp         # writes on, allowlist-scoped
The API key comes from the MINTSOFT_API_KEY env var (see mintsoft_client for the
optional GCP Secret Manager fallback).
"""
from __future__ import annotations

import os
from datetime import date, timedelta

from mcp.server.fastmcp import FastMCP

from .mintsoft_client import MintsoftClient, Result, write_allowlist
from . import discontinue_tools as dt
from . import bundle_tools as bt

mcp = FastMCP("mintsoft")

# ---- write gating -----------------------------------------------------------
WRITES_ENABLED = os.environ.get("MINTSOFT_ENABLE_WRITES", "0").lower() in ("1", "true", "yes", "on")
WRITE_ALLOWLIST = write_allowlist()

# ---- lazy client (no key needed just to list tools) -------------------------
_client: MintsoftClient | None = None


def client() -> MintsoftClient:
    global _client
    if _client is None:
        _client = MintsoftClient()
    return _client


def _out(res: Result):
    """Normalise a Result for tool output. Surfaces Mintsoft's Success=False (HTTP 200) as an error."""
    if not res.ok:
        return {"error": True, "http_status": res.status, "detail": res.summary}
    data = res.data
    # Mintsoft returns validation failures as HTTP 200 with Success=False.
    if isinstance(data, dict) and data.get("Success") is False:
        return {"error": True, "http_status": res.status,
                "detail": data.get("Message") or "Success=False", "raw": data}
    return data


def _assert_client_allowed(client_id):
    if client_id is None or int(client_id) not in WRITE_ALLOWLIST:
        raise ValueError(
            f"Write blocked: ClientId {client_id} is not in the write allowlist {sorted(WRITE_ALLOWLIST)}. "
            "This is a live WMS; set MINTSOFT_WRITE_CLIENT_ALLOWLIST to the ClientIds you deliberately intend to write to."
        )


def _cap(limit: int) -> int:
    return max(1, min(int(limit), 100))  # Mintsoft Limit max = 100


# =============================================================================
# READ TOOLS (always on)
# =============================================================================

# ---- Orders ----
@mcp.tool()
def get_order(order_id: int) -> dict:
    """Get full detail for one Mintsoft order by its numeric order ID."""
    return _out(client().get(f"Order/{order_id}"))


@mcp.tool()
def search_orders(order_number: str, exact: bool = True) -> list | dict:
    """Search orders by order number. exact=True does an exact match (recommended)."""
    return _out(client().get("Order/Search", {"OrderNumber": order_number, "exactMatch": exact}))


@mcp.tool()
def get_order_id(order_number: str) -> dict:
    """Resolve an exact order number to its internal Mintsoft order ID."""
    return _out(client().get("Order/GetOrderId", {"orderNumber": order_number}))


@mcp.tool()
def list_orders(status_id: int, page: int = 1, limit: int = 50) -> list | dict:
    """List orders by status ID (e.g. NEW=1, DESPATCHED=4, QUERYRAISED=13, AWAITINGPICKING=15, PICKED=17). limit max 100."""
    return _out(client().get("Order/List", {"OrderStatusId": status_id, "PageNo": page, "Limit": _cap(limit)}))


@mcp.tool()
def list_order_statuses() -> list | dict:
    """List all order status names and their IDs."""
    return _out(client().get("Order/Statuses"))


@mcp.tool()
def get_order_items(order_id: int) -> list | dict:
    """Get the line items for an order."""
    return _out(client().get(f"Order/{order_id}/Items"))


@mcp.tool()
def get_order_comments(order_id: int) -> list | dict:
    """Get the comments logged against an order."""
    return _out(client().get(f"Order/{order_id}/Comments"))


@mcp.tool()
def track_order_by_tracking_number(tracking_number: str) -> dict:
    """Find an order by its courier tracking number."""
    return _out(client().get("Order/TrackingNumberSearch", {"TrackingNumber": tracking_number}))


@mcp.tool()
def list_order_channels() -> list | dict:
    """List the sales channels (Shopify, Amazon, etc.) configured in Mintsoft."""
    return _out(client().get("Order/Channels"))


# ---- Products / stock ----
@mcp.tool()
def get_product(product_id: int) -> dict:
    """Get full detail for one product by its numeric product ID."""
    return _out(client().get(f"Product/{product_id}"))


@mcp.tool()
def lookup_product_id(sku: str) -> dict:
    """Resolve a primary SKU to its internal product ID."""
    return _out(client().get("Product/LookupProductId", {"SKU": sku}))


@mcp.tool()
def search_products(query: str) -> list | dict:
    """Search products by SKU or name."""
    return _out(client().get("Product/Search", {"Search": query}))


@mcp.tool()
def list_products(page: int = 1, limit: int = 50, client_id: int = 0) -> list | dict:
    """List products (paged). limit max 100. client_id filters to one client (0 = all).
    Unlike search_products, this listing INCLUDES bundles — use it to find a bundle's product ID."""
    p = {"PageNo": page, "Limit": _cap(limit)}
    if client_id:
        p["ClientId"] = client_id
    return _out(client().get("Product/List", p))


@mcp.tool()
def get_alt_codes(product_id: int) -> list | dict:
    """List the alternative SKUs / barcodes (AltCodes) mapped to a product."""
    return _out(client().get(f"Product/{product_id}/AltCodes"))


@mcp.tool()
def get_bundle(product_id: int) -> dict:
    """Read a bundle's make-up: SKU, Name, and Components (each with ProductId, SKU, Quantity).
    NB: bundles are hidden from Product/Search — find them via list_products(client_id=…) or by ID."""
    return _out(client().get(f"Product/{product_id}/Bundle"))


@mcp.tool()
def get_product_inventory(product_id: int) -> list | dict:
    """Get inventory (stock by location) for a product."""
    return _out(client().get(f"Product/{product_id}/Inventory"))


@mcp.tool()
def get_stock_levels_by_warehouse(warehouse_id: int, updated_since_days: int = 31) -> list | dict:
    """Stock levels for a warehouse, for products updated in the last N days (default 31)."""
    since = (date.today() - timedelta(days=updated_since_days)).isoformat()
    return _out(client().get("Product/StockLevelsByWarehouse",
                             {"WarehouseId": warehouse_id, "LastUpdatedSince": since}))


@mcp.tool()
def get_stock_levels_updated_since(days: int = 7) -> list | dict:
    """Stock levels for products changed in the last N days (default 7). Prefer this over the full StockLevels (which can time out)."""
    since = (date.today() - timedelta(days=days)).isoformat()
    return _out(client().get("Product/StockLevels/UpdatedSince", {"FromDate": since}))


@mcp.tool()
def get_stock_flow(product_id: int, days: int = 30) -> list | dict:
    """Stock-flow (movement) history for a product over the last N days (default 30)."""
    to_d = date.today().isoformat()
    from_d = (date.today() - timedelta(days=days)).isoformat()
    return _out(client().get(f"Product/{product_id}/StockFlow", {"FromDate": from_d, "ToDate": to_d}))


@mcp.tool()
def list_suppliers() -> list | dict:
    """List product suppliers."""
    return _out(client().get("Product/Suppliers"))


@mcp.tool()
def precheck_discontinue(sku: str = "", product_id: int = 0, client_id: int = 0) -> dict:
    """READ-ONLY discontinue footprint for a SKU before retiring it: stock on hand, live-order
    allocations, inbound/backorder, bundle membership, plus alt-code & replen info. Returns a
    blocker list and a `clear` flag. Identify by sku (+client_id) or product_id. Safe on any client."""
    return dt.precheck_discontinue(client(), sku or None,
                                   product_id=product_id or None, client_id=client_id or None)


# ---- Inbound / returns / warehouse ----
@mcp.tool()
def list_asns(page: int = 1, limit: int = 50) -> list | dict:
    """List ASNs (inbound goods-in / advance shipping notices)."""
    return _out(client().get("ASN/List", {"PageNo": page, "Limit": _cap(limit)}))


@mcp.tool()
def get_asn(asn_id: int) -> dict:
    """Get one ASN (inbound) by ID."""
    return _out(client().get(f"ASN/{asn_id}"))


@mcp.tool()
def list_returns(page: int = 1, limit: int = 50) -> list | dict:
    """List returns."""
    return _out(client().get("Return/List", {"PageNo": page, "Limit": _cap(limit)}))


@mcp.tool()
def get_return(return_id: int) -> dict:
    """Get one return by ID."""
    return _out(client().get(f"Return/{return_id}"))


@mcp.tool()
def list_warehouses() -> list | dict:
    """List warehouses."""
    return _out(client().get("Warehouse"))


@mcp.tool()
def list_locations(warehouse_id: int) -> list | dict:
    """List all storage locations in a warehouse."""
    return _out(client().get(f"Warehouse/{warehouse_id}/Location/All"))


@mcp.tool()
def list_warehouse_zones(warehouse_id: int) -> list | dict:
    """List the zones (location-charge zones) of a warehouse."""
    return _out(client().get(f"Warehouse/{warehouse_id}/Zones"))


# ---- Clients / couriers / reports / reference ----
@mcp.tool()
def list_clients() -> list | dict:
    """List clients (brands) in the account."""
    return _out(client().get("Client"))


@mcp.tool()
def list_couriers() -> list | dict:
    """List courier services."""
    return _out(client().get("Courier/Services"))


@mcp.tool()
def list_invoices(page: int = 1, limit: int = 50) -> list | dict:
    """List accounting invoices (needs Accounting permission on the API key)."""
    return _out(client().get("Accounting/Invoice/List", {"PageNo": page, "Limit": _cap(limit)}))


@mcp.tool()
def get_invoice(invoice_id: int) -> dict:
    """Get one invoice by ID (needs Accounting permission)."""
    return _out(client().get(f"Accounting/Invoice/{invoice_id}"))


@mcp.tool()
def list_order_rules() -> list | dict:
    """List order rules (needs OrderRules permission on the API key)."""
    return _out(client().get("OrderRules"))


@mcp.tool()
def run_report(kind: str, from_date: str = "", to_date: str = "",
               warehouse_id: int = 0, client_id: int = 0) -> list | dict:
    """Run a Mintsoft report. kind = 'courier_despatch' | 'products_in_location' | 'product_usage'.
    Dates are YYYY-MM-DD; default to the last 7 days when omitted. warehouse_id/client_id are optional filters (0 = all)."""
    fd = from_date or (date.today() - timedelta(days=7)).isoformat()
    td = to_date or date.today().isoformat()
    if kind == "courier_despatch":
        p = {"DespatchDateStart": fd, "DespatchDateEnd": td}
        if warehouse_id:
            p["WarehouseId"] = warehouse_id
        return _out(client().get("Reports/CourierDespatchReport", p))
    if kind == "products_in_location":
        p = {}
        if warehouse_id:
            p["warehouseId"] = warehouse_id
        if client_id:
            p["clientId"] = client_id
        return _out(client().get("Reports/ProductsInLocationReport", p))
    if kind == "product_usage":
        p = {"fromDate": fd, "toDate": td}
        if warehouse_id:
            p["warehouseId"] = warehouse_id
        if client_id:
            p["clientId"] = client_id
        return _out(client().get("Reports/ProductUsageReport", p))
    return {"error": True, "detail": f"unknown report kind '{kind}'"}


@mcp.tool()
def get_ref_data(kind: str) -> list | dict:
    """Reference data. kind = 'countries' | 'currencies' | 'picking_types' | 'price_types'."""
    path = {"countries": "RefData/Countries", "currencies": "RefData/Currencies",
            "picking_types": "RefData/PickingTypes", "price_types": "RefData/PriceTypes"}.get(kind)
    if not path:
        return {"error": True, "detail": f"unknown ref kind '{kind}'"}
    return _out(client().get(path))


@mcp.tool()
def mintsoft_get(path: str, params: dict | None = None) -> object:
    """Generic READ-ONLY passthrough: GET any Mintsoft API path (e.g. 'Order/123/Shipments').
    Path is relative to /api/. Only GETs — for safety this tool cannot perform writes."""
    clean = path.lstrip("/")
    if clean.lower().startswith("api/"):
        clean = clean[4:]
    return _out(client().request("GET", clean, params=params))


@mcp.tool()
def get_api_audit_log(limit: int = 50, writes_only: bool = False, contains: str = "") -> list | dict:
    """Read the most recent entries from the Mintsoft API audit log — the append-only history of
    EVERY call this tooling/server makes (ts, method, write flag, path, body, status, success,
    affected_id). Use to trace or recover from changes. writes_only=True → only PUT/POST/DELETE;
    contains → only lines whose path/body matches the substring (e.g. a SKU or 'Bundle')."""
    import json as _j
    from .mintsoft_client import AUDIT_PATH
    try:
        with open(AUDIT_PATH, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return {"info": "no audit log yet", "path": AUDIT_PATH}
    rows = []
    for ln in lines:
        try:
            d = _j.loads(ln)
        except ValueError:
            continue
        if writes_only and not d.get("write"):
            continue
        if contains and contains.lower() not in _j.dumps(d).lower():
            continue
        rows.append(d)
    return {"path": AUDIT_PATH, "total_matched": len(rows), "showing": rows[-_cap_log(limit):]}


def _cap_log(n: int) -> int:
    return max(1, min(int(n), 500))


# =============================================================================
# WRITE TOOLS (registered only when MINTSOFT_ENABLE_WRITES is truthy)
# =============================================================================
if WRITES_ENABLED:

    @mcp.tool()
    def add_order_comment(order_id: int, comment: str) -> dict:
        """[WRITE] Add a comment to an order. Order must belong to an allowlisted client."""
        owner = client().get(f"Order/{order_id}")
        cid = owner.data.get("ClientId") if isinstance(owner.data, dict) else None
        _assert_client_allowed(cid)
        return _out(client().request("POST", f"Order/{order_id}/Comments", json={"Comment": comment}))

    @mcp.tool()
    def add_order_tag(order_id: int, tag: str) -> dict:
        """[WRITE] Add a tag to an order. Order must belong to an allowlisted client."""
        owner = client().get(f"Order/{order_id}")
        cid = owner.data.get("ClientId") if isinstance(owner.data, dict) else None
        _assert_client_allowed(cid)
        return _out(client().request("PUT", f"Order/{order_id}/Tag", json={"Tag": tag}))

    @mcp.tool()
    def create_product(sku: str, weight: float, client_id: int, name: str = "",
                       fields: dict | None = None) -> dict:
        """[WRITE] Create a product. Required: sku, weight, client_id (must be allowlisted).
        Optional `fields` dict for any other Product field (Price, EAN, Description, expiry flags, etc.).
        NB: PalletSizes needs valid codes; UnNumber max length 4; validation errors come back as Success=False.
        CommodityCode may be passed as a plain string (e.g. "470710") — the client coerces it to the
        required nested object {"Code": ...}; a raw string is otherwise silently dropped by Mintsoft."""
        _assert_client_allowed(client_id)
        payload = {"SKU": sku, "Weight": weight, "ClientId": client_id}
        if name:
            payload["Name"] = name
        if fields:
            payload.update(fields)
        return _out(client().request("PUT", "Product", json=payload))

    @mcp.tool()
    def update_product(product_id: int, fields: dict) -> dict:
        """[WRITE] Update a product. `fields` holds the columns to change. Product must belong to an allowlisted client."""
        cur = client().get(f"Product/{product_id}")
        cid = cur.data.get("ClientId") if isinstance(cur.data, dict) else None
        _assert_client_allowed(cid)
        payload = {"ID": product_id, "ClientId": cid}
        payload.update(fields)
        return _out(client().request("POST", "Product", json=payload))

    @mcp.tool()
    def add_alt_code(product_id: int, sku: str, connect_mapping_type: str = "Amazon") -> dict:
        """[WRITE] Add an alternative SKU / barcode (AltCode) to a product. Product must belong to an allowlisted client.
        connect_mapping_type is a STRING channel name from Mintsoft's enum (e.g. 'Amazon', 'Shopify',
        'Ebay', 'WooCommerce', 'Magento', 'API') — NOT an integer, despite what Swagger says."""
        cur = client().get(f"Product/{product_id}")
        cid = cur.data.get("ClientId") if isinstance(cur.data, dict) else None
        _assert_client_allowed(cid)
        # AltCode POST takes QUERY params (SKU, ConnectMappingType-string), not a JSON body.
        return _out(client().request("POST", f"Product/{product_id}/AltCodes",
                                     params={"SKU": sku, "ConnectMappingType": connect_mapping_type}))

    @mcp.tool()
    def delete_product(product_id: int, confirm: bool = False) -> dict:
        """[WRITE][DESTRUCTIVE] Delete a product. Requires confirm=True AND the product to belong to an allowlisted client."""
        if not confirm:
            return {"error": True, "detail": "Refused: pass confirm=True to delete. This is irreversible on the live WMS."}
        cur = client().get(f"Product/{product_id}")
        cid = cur.data.get("ClientId") if isinstance(cur.data, dict) else None
        _assert_client_allowed(cid)
        return _out(client().request("DELETE", f"Product/{product_id}"))

    @mcp.tool()
    def amend_bundle(new_components: list, product_id: int = 0, sku: str = "", client_id: int = 0,
                     new_name: str = "", new_description: str = "", force: bool = False,
                     allow_live: bool = False) -> dict:
        """[WRITE] Edit a bundle — change its components, and optionally its name/description — via
        delete+recreate. ⚠️ the bundle gets a NEW ProductId (references by SKU survive; alt codes /
        order-history tied to the old ID do not). Refuses if the bundle has alt codes or recent stock
        unless force=True. Allowlisted clients only unless allow_live=True (explicit override).
        new_components = [{"ProductId": int, "Quantity": int}, ...]; identify the bundle by product_id
        or sku+client_id. new_name/new_description optional (only new_name → description mirrors it)."""
        return bt.amend_bundle(client(), new_components, product_id=product_id or None,
                               sku=sku or None, client_id=client_id or None,
                               new_name=new_name or None, new_description=new_description or None,
                               force=force, allow_live=allow_live)

    @mcp.tool()
    def discontinue_sku(sku: str = "", product_id: int = 0, client_id: int = 0, force: bool = False) -> dict:
        """[WRITE] Discontinue a SKU: runs the footprint pre-check, then renames
        SKU + Name to '[Discontinued] …' and sets DisCont=true (same ProductId — alt codes/bundles/history
        kept). Refuses if there are unresolved blockers (stock / live orders / inbound / bundle membership)
        unless force=True. WRITES ONLY on allowlisted clients; on any other client it returns the
        footprint + the exact Mintsoft-UI instruction and makes no change."""
        return dt.discontinue_sku(client(), sku or None,
                                  product_id=product_id or None, client_id=client_id or None, force=force)


def main() -> None:
    """Console entry point (`mintsoft-mcp`)."""
    mcp.run()


if __name__ == "__main__":
    main()
