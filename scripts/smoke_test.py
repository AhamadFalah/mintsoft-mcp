"""Mintsoft API smoke tester.

Verifies endpoints are reachable & responding, tiered by side-effect so we never
mutate live customer data:

  A  reference / collection GETs ............. run live, read-only          (safe)
  B  detail GETs needing a harvested ID ...... run live, read-only          (safe)
  C  state-change GETs (Cancel/Mark*/...) .... listed; --write + test client only
  D  writes (POST/PUT/DELETE) ................ listed; --write + test client only

Default run = Tiers A+B only (read-only, safe on production).
`--write` additionally runs a SMALL, self-cleaning C/D demo strictly scoped to the
"test client" (create product -> read back -> delete). Everything else in C/D is
reported as MANUAL so coverage is honest, not silently skipped.

Usage:
  python smoke_test.py                 # safe read-only pass
  python smoke_test.py --write         # + guarded test-client write demo
  python smoke_test.py --json out.json # also dump machine-readable results
"""
from __future__ import annotations

import os
import sys
import json
from datetime import date, timedelta

from mintsoft_mcp.mintsoft_client import MintsoftClient, Result

TODAY = date.today()
WEEK_AGO = (TODAY - timedelta(days=7)).isoformat()
MONTH_AGO = (TODAY - timedelta(days=31)).isoformat()
TODAY_S = TODAY.isoformat()

# ---------------------------------------------------------------------------
# Full 187-endpoint registry. tier: A/B/C/D. For A/B we give concrete params or
# a {placeholder} path filled from harvested IDs. C/D carry a note only.
# ---------------------------------------------------------------------------
# Each row: (method, path, tier, params_or_None, note)
REG: list[tuple] = [
    # ---- Auth (handled separately by client construction) ----
    ("POST", "Auth", "A", None, "auth handled at client init"),

    # ---- Accounting (15) ----
    ("GET", "Accounting/Invoice/List", "A", None, ""),
    ("GET", "Accounting/Invoice/All", "A", None, ""),
    ("GET", "Accounting/Invoice/{invoice_id}", "B", None, ""),
    ("GET", "Accounting/Invoice/{invoice_id}/Orders", "B", None, ""),
    ("GET", "Accounting/Invoice/Orders/{order_id}", "B", None, ""),
    ("POST", "Accounting/AddCourierCost", "D", None, "adds cost to invoice item"),
    ("GET", "Accounting/Invoice/{invoice_id}/Returns", "B", None, ""),
    ("GET", "Accounting/Invoice/{invoice_id}/GoodsIn", "B", None, ""),
    ("GET", "Accounting/Invoice/{invoice_id}/Collections", "B", None, ""),
    ("GET", "Accounting/Invoice/{invoice_id}/Other", "B", None, ""),
    ("GET", "Account/Invoice/InvoiceItemTypes", "A", None, ""),
    ("POST", "Account/Invoice/AddAdditionalInvoiceItem", "D", None, "adds invoice item"),
    ("POST", "Accounting/LocationTypeStoragePrice", "D", None, "sets storage price"),
    ("GET", "Account/Invoice/GetUnconfirmedInvoiceSummary", "B",
     {"clientID": "{test_client_id}", "fromDate": MONTH_AGO, "toDate": TODAY_S}, "test client"),
    ("GET", "Account/Invoice/GetUnconfirmedInvoiceStorageCosts", "B",
     {"clientID": "{test_client_id}", "fromDate": MONTH_AGO, "toDate": TODAY_S}, "test client"),

    # ---- ASN (16) ----
    ("PUT", "ASN", "D", None, "create ASN"),
    ("GET", "ASN/{asn_id}", "B", None, ""),
    ("POST", "ASN/{asn_id}", "D", None, "update ASN"),
    ("DELETE", "ASN/{asn_id}", "D", None, "delete ASN"),
    ("GET", "ASN/List", "A", None, ""),
    ("PUT", "ASN/{asn_id}/ConnectActions", "D", None, "register webhook"),
    ("GET", "ASN/Statuses", "A", None, ""),
    ("GET", "ASN/GoodsInTypes", "A", None, ""),
    ("GET", "ASN/{asn_id}/Confirm", "C", None, "confirm ASN"),
    ("GET", "ASN/{asn_id}/BookIn", "C", None, "book in (admin)"),
    ("GET", "ASN/{asn_id}/BookInPartial", "C", None, "book in partial (admin)"),
    ("GET", "ASN/{asn_id}/PartBook", "C", None, "part book (admin)"),
    ("GET", "ASN/{asn_id}/Paperwork", "B", None, ""),
    ("POST", "ASN/{asn_id}/Items/Receive", "D", None, "receive items"),
    ("GET", "ASN/{asn_id}/MarkAwaitingPutAway", "C", None, "state change"),
    ("GET", "ASN/{asn_id}/MarkPutAwayComplete", "C", None, "state change"),

    # ---- Batch (10) ----
    ("POST", "Batch", "D", None, "create batch"),
    ("POST", "Batch/AssignOrderToUser", "D", None, "assign"),
    ("POST", "Batch/AssignBatchToUser", "D", None, "assign"),
    ("GET", "Batch/List", "A", None, ""),
    ("GET", "Batch/{batch_id}", "B", None, ""),
    ("PUT", "Batch/StatusUpdate/{batch_id}", "D", None, "status update"),
    ("POST", "Batch/DespatchBatch", "C", None, "despatch batch"),
    ("GET", "Batch/Templates", "A", None, ""),
    ("POST", "Batch/CreateBatchFromTemplate", "D", None, "create batch"),
    ("DELETE", "Batch/DeleteBatch", "D", None, "delete batch"),

    # ---- Client (5) ----
    ("GET", "Client", "A", None, ""),
    ("POST", "Client", "D", None, "create client"),
    ("POST", "Client/UpdateClient", "D", None, "update client"),
    ("GET", "Client/SubClients", "A", None, ""),
    ("POST", "Client/SubClients", "D", None, "add sub-client"),

    # ---- Courier (4) ----
    ("GET", "Courier/Services", "A", None, ""),
    ("GET", "Courier/Services/{service_id}", "B", None, ""),
    ("GET", "Courier/ServiceTypes", "A", None, ""),
    ("GET", "Courier/Services/{service_id}/ExternalNames", "B", None, ""),

    # ---- Order (55) ----
    ("GET", "Order/Statuses", "A", None, ""),
    ("GET", "Order/Documents/DocumentTypes", "A", None, ""),
    ("GET", "Order/Documents/PaperSizes", "A", None, ""),
    ("GET", "Order/Shipments/TrackingEvents/Statuses", "A", None, ""),
    ("GET", "Order/Shipments/TrackingEvents/List", "A", None, ""),
    ("PUT", "Order", "D", None, "create order"),
    ("GET", "Order/{order_id}", "B", None, ""),
    ("POST", "Order/{order_id}", "D", None, "update order"),
    ("GET", "Order/Search", "B", {"OrderNumber": "{order_number}", "exactMatch": True}, ""),
    ("GET", "Order/GetOrderId", "B", {"OrderNumber": "{order_number}"}, ""),
    ("GET", "Order/TrackingNumberSearch", "C", None, "needs a tracking number"),
    ("GET", "Order/Channels", "A", None, ""),
    ("GET", "Order/List", "A", {"OrderStatusId": 4}, "DESPATCHED sample"),
    ("GET", "Order/{order_id}/Items", "B", None, ""),
    ("PUT", "Order/{order_id}/Items", "D", None, "add item"),
    ("POST", "Order/{order_id}/Items/{item_id}", "D", None, "update item"),
    ("DELETE", "Order/{order_id}/Items/{item_id}", "D", None, "delete item"),
    ("GET", "Order/{order_id}/Cancel", "C", None, "cancels order"),
    ("GET", "Order/{order_id}/CancelNoStock", "C", None, "cancels order"),
    ("GET", "Order/{order_id}/MarkDespatched", "C", None, "state change"),
    ("GET", "Order/{order_id}/MarkPrinted", "C", None, "state change"),
    ("GET", "Order/{order_id}/UpdateNumberOfParcels", "C", None, "state change"),
    ("GET", "Order/{order_id}/MarkAwaitingPayment", "C", None, "state change"),
    ("GET", "Order/{order_id}/MarkPaymentReceived", "C", None, "state change"),
    ("GET", "Order/{order_id}/MarkAwaitingConfirmation", "C", None, "state change"),
    ("GET", "Order/{order_id}/MarkConfirmed", "C", None, "state change"),
    ("GET", "Order/{order_id}/MarkPackAndHold", "C", None, "state change"),
    ("GET", "Order/{order_id}/MarkReleasePackAndHold", "C", None, "state change"),
    ("GET", "Order/{order_id}/DespatchNote", "B", None, ""),
    ("GET", "Order/{order_id}/Comments", "B", None, ""),
    ("POST", "Order/{order_id}/Comments", "D", None, "add comment (low risk)"),
    ("GET", "Order/{order_id}/Splits", "B", None, ""),
    ("GET", "Order/{order_id}/Documents", "B", None, ""),
    ("PUT", "Order/{order_id}/Documents", "D", None, "upload doc"),
    ("GET", "Order/{order_id}/Documents/{document_id}", "B", None, ""),
    ("GET", "Order/{order_id}/Documents/{document_id}/Data", "B", None, ""),
    ("GET", "Order/{order_id}/Documents/{document_id}/MarkPrinted", "C", None, "state change"),
    ("GET", "Order/{order_id}/BoxPackingList", "B", None, ""),
    ("PUT", "Order/{order_id}/Tag", "D", None, "add tag (low risk)"),
    ("DELETE", "Order/{order_id}/Tag", "D", None, "delete tag"),
    ("GET", "Order/{order_id}/Shipments", "B", None, ""),
    ("PUT", "Order/{order_id}/Shipments", "D", None, "upload shipment"),
    ("DELETE", "Order/{order_id}/Shipments/{order_shipment_id}", "D", None, "delete shipment"),
    ("POST", "Order/{order_id}/Shipments/CreateShipment", "C", None, "books a courier label"),
    ("DELETE", "Order/{order_id}/Shipments/CancelShipment", "C", None, "cancels label"),
    ("GET", "Order/{order_id}/ConnectActions", "B", None, ""),
    ("PUT", "Order/{order_id}/ConnectActions", "D", None, "upload connect action"),
    ("GET", "Order/{order_id}/Allocations", "B", None, ""),
    ("GET", "Order/{order_id}/BarcodeVerifiedOrderItems", "B", None, ""),
    ("GET", "Order/{order_id}/Queries", "B", None, ""),
    ("GET", "Order/{order_id}/Queries/{query_id}/Comments", "B", None, ""),
    ("POST", "Order/{order_id}/SplitOrderItems", "D", None, "split items"),
    ("GET", "Order/{order_id}/BillingAddress", "B", None, ""),
    ("POST", "Order/{order_id}/MarkFraudRisk", "D", None, "flag fraud"),
    ("POST", "Order/{order_id}/MarkFraudRiskAccepted", "D", None, "flag fraud"),

    # ---- OrderRules (11) ----
    ("GET", "OrderRules", "A", None, ""),
    ("PUT", "OrderRules", "D", None, "update rule"),
    ("POST", "OrderRules", "D", None, "add rule"),
    ("DELETE", "OrderRules/{rule_id}", "D", None, "delete rule"),
    ("POST", "OrderRules/{rule_id}/Activate", "C", None, "activate rule"),
    ("POST", "OrderRules/{rule_id}/Deactivate", "C", None, "deactivate rule"),
    ("POST", "OrderRules/{rule_id}/Conditions", "D", None, "add condition"),
    ("DELETE", "OrderRules/Conditions/{condition_id}", "D", None, "delete condition"),
    ("GET", "OrderRules/Actions", "A", None, ""),
    ("GET", "OrderRules/ConditionOptions", "A", None, ""),
    ("POST", "OrderRules/Run/{order_id}", "C", None, "re-runs rules on order"),

    # ---- Product (31) ----
    ("PUT", "Product", "D", None, "create product"),
    ("POST", "Product", "D", None, "update product"),
    ("GET", "Product/{product_id}", "B", None, ""),
    ("DELETE", "Product/{product_id}", "D", None, "delete product"),
    ("PUT", "Product/ProductPrices", "D", None, "set prices"),
    ("DELETE", "Product/ProductPrices", "D", None, "delete prices"),
    ("GET", "Product/List", "A", None, ""),
    ("PUT", "Product/Bundle", "D", None, "create bundle"),
    ("GET", "Product/{product_id}/Bundle", "B", None, ""),
    ("GET", "Product/{product_id}/StockFlow", "B", {"FromDate": MONTH_AGO, "ToDate": TODAY_S}, ""),
    ("GET", "Product/{product_id}/StockFlow/Filtered", "B", {"FromDate": MONTH_AGO, "ToDate": TODAY_S}, ""),
    ("GET", "Product/{product_id}/Cartons", "B", None, ""),
    ("POST", "Product/{product_id}/Cartons", "D", None, "add cartons"),
    ("DELETE", "Product/{product_id}/Carton/{carton_id}", "D", None, "delete carton"),
    ("GET", "Product/{product_id}/AltCodes", "B", None, "alt SKUs"),
    ("POST", "Product/{product_id}/AltCodes", "D", None, "add alt SKU"),
    ("GET", "Product/{product_id}/Inventory", "B", None, ""),
    ("GET", "Product/Inventory/Bulk", "B", {"ProductIds": "{product_id}"}, ""),
    ("GET", "Product/Search", "B", {"Search": "{product_sku}"}, ""),
    ("GET", "Product/LookupProductId", "B", {"SKU": "{product_sku}"}, ""),
    ("GET", "Product/SearchBarcode", "C", None, "needs a barcode"),
    ("GET", "Product/StockLevels", "A", None, ""),
    ("GET", "Product/StockLevelsByWarehouse", "B",
     {"WarehouseId": "{warehouse_id}", "LastUpdatedSince": MONTH_AGO}, ""),
    ("GET", "Product/{product_id}/Inventory/PreOrderBreakdown", "B", {"WarehouseId": "{warehouse_id}"}, ""),
    ("GET", "Product/{product_id}/Inventory/PreOrderBreakdown/All", "B", None, ""),
    ("GET", "Product/UpdatedSince", "A", {"FromDate": WEEK_AGO}, ""),
    ("GET", "Product/StockLevels/UpdatedSince", "A", {"FromDate": WEEK_AGO}, ""),
    ("GET", "Product/Suppliers", "A", None, ""),
    ("PUT", "Product/Suppliers", "D", None, "create supplier"),
    ("POST", "Product/BulkOnHandStockUpdate", "D", None, "corrects stock"),
    ("POST", "Product/{product_id}/CustomFields", "D", None, "add custom field"),

    # ---- RefData (4) ----
    ("GET", "RefData/Countries", "A", None, ""),
    ("GET", "RefData/Currencies", "A", None, ""),
    ("GET", "RefData/PickingTypes", "A", None, ""),
    ("GET", "RefData/PriceTypes", "A", None, ""),

    # ---- ReplenPoint (4) ----
    ("GET", "ReplenPoints", "A", None, ""),
    ("POST", "ReplenPoint", "D", None, "add replen point"),
    ("PUT", "ReplenPoint/{replen_id}", "D", None, "update replen point"),
    ("DELETE", "ReplenPoint/{replen_id}", "D", None, "delete replen point"),

    # ---- Reports (3) ----
    ("GET", "Reports/CourierDespatchReport", "A",
     {"DespatchDateStart": WEEK_AGO, "DespatchDateEnd": TODAY_S}, ""),
    ("GET", "Reports/ProductsInLocationReport", "A", None, ""),
    ("GET", "Reports/ProductUsageReport", "A", {"FromDate": WEEK_AGO, "ToDate": TODAY_S}, ""),

    # ---- Return (10) ----
    ("GET", "Return/Reasons", "A", None, ""),
    ("GET", "Return/{return_id}", "B", None, ""),
    ("GET", "Return/{return_id}/Items", "B", None, ""),
    ("GET", "Return/SearchByDate", "A", {"Date": WEEK_AGO}, ""),
    ("GET", "Return/List", "A", None, ""),
    ("POST", "Return/CreateReturn/{order_id}", "D", None, "create return"),
    ("POST", "Return/CreateExternalReturn", "D", None, "create external return"),
    ("POST", "Return/{return_id}/AddItem", "D", None, "add item"),
    ("POST", "Return/{return_id}/AllocateItemLocation", "D", None, "allocate"),
    ("POST", "Return/{return_id}/Confirm", "C", None, "confirm return"),

    # ---- Warehouse + WarehouseTransfer (18) ----
    ("GET", "Warehouse", "A", None, ""),
    ("GET", "Warehouse/LocationTypes", "A", None, ""),
    ("GET", "Warehouse/{warehouse_id}/Location/All", "B", None, ""),
    ("GET", "Warehouse/{warehouse_id}/Location/{location_id}", "B", None, ""),
    ("GET", "Warehouse/{warehouse_id}/Location/LookupLocationId", "C", None, "needs a location code"),
    ("GET", "Warehouse/{warehouse_id}/StockMovements", "B", None, ""),
    ("POST", "Warehouse/StockMovement", "D", None, "moves stock"),
    ("POST", "Warehouse/BulkStockMovement", "D", None, "moves stock"),
    ("GET", "Warehouse/{warehouse_id}/Zones", "B", None, ""),
    ("GET", "Warehouse/{warehouse_id}/ToteBreakdown", "C", None, "needs a tote id"),
    ("PUT", "WarehouseTransfer", "D", None, "create transfer"),
    ("GET", "WarehouseTransfer/List", "A", None, ""),
    ("GET", "WarehouseTransfer/{transfer_id}", "B", None, ""),
    ("POST", "WarehouseTransfer/{transfer_id}", "D", None, "update transfer"),
    ("DELETE", "WarehouseTransfer/{transfer_id}", "D", None, "delete transfer"),
    ("GET", "WarehouseTransfer/Statuses", "A", None, ""),
    ("GET", "WarehouseTransfer/{transfer_id}/Confirm", "C", None, "confirm transfer"),
    ("GET", "WarehouseTransfer/{transfer_id}/Cancel", "C", None, "cancel transfer"),
]


def harvest(c: MintsoftClient) -> dict:
    """Pull one real id of each type so Tier-B detail GETs have something to hit."""
    ids: dict[str, object] = {}

    clients = c.get("Client").data or []
    if isinstance(clients, list) and clients:
        ids["client_id"] = _first_id(clients[0])
        # Write demo target: MINTSOFT_TEST_CLIENT_ID env, else a client whose name
        # contains "test". NEVER falls back to a real client — no target, no writes.
        env_tc = os.environ.get("MINTSOFT_TEST_CLIENT_ID")
        tc = next((x for x in clients
                   if "test" in str(x.get("Name", "")).lower()), None)
        ids["test_client_id"] = int(env_tc) if env_tc else (_first_id(tc) if tc else None)

    orders = c.paged("Order/List", {"OrderStatusId": 4}, max_pages=1)
    if orders:
        o = orders[0]
        ids["order_id"] = _first_id(o)
        ids["order_number"] = o.get("OrderNumber")
        items = c.get(f"Order/{ids['order_id']}/Items").data or []
        if items:
            ids["item_id"] = _first_id(items[0])
        docs = c.get(f"Order/{ids['order_id']}/Documents").data or []
        if docs:
            ids["document_id"] = _first_id(docs[0])

    products = c.paged("Product/List", max_pages=1)
    if products:
        p = products[0]
        ids["product_id"] = _first_id(p)
        ids["product_sku"] = p.get("SKU") or p.get("Code")

    for key, path, idkey in [
        ("asn_id", "ASN/List", None),
        ("return_id", "Return/List", None),
        ("batch_id", "Batch/List", None),
        ("invoice_id", "Accounting/Invoice/List", None),
        ("transfer_id", "WarehouseTransfer/List", None),
    ]:
        rows = c.get(path).data or []
        if isinstance(rows, list) and rows:
            ids[key] = _first_id(rows[0])

    whs = c.get("Warehouse").data or []
    if isinstance(whs, list) and whs:
        ids["warehouse_id"] = _first_id(whs[0])
        locs = c.get(f"Warehouse/{ids['warehouse_id']}/Location/All").data or []
        if isinstance(locs, list) and locs:
            ids["location_id"] = _first_id(locs[0])

    svcs = c.get("Courier/Services").data or []
    if isinstance(svcs, list) and svcs:
        ids["service_id"] = _first_id(svcs[0])

    return {k: v for k, v in ids.items() if v is not None}


def _first_id(obj: dict):
    if not isinstance(obj, dict):
        return None
    # NB: PUT /api/Product returns the new id as "ProductId", not "ID".
    for k in ("ID", "Id", "id", "ProductId"):
        if obj.get(k) is not None:
            return obj[k]
    return None


def _fill(template, ids: dict):
    """Substitute {placeholders} in a path or params dict. Returns None if any unfilled."""
    if isinstance(template, str):
        out = template
        for k, v in ids.items():
            out = out.replace("{" + k + "}", str(v))
        return None if "{" in out else out
    if isinstance(template, dict):
        out = {}
        for k, v in template.items():
            fv = _fill(v, ids) if isinstance(v, str) and "{" in str(v) else v
            if fv is None:
                return None
            out[k] = fv
        return out
    return template


def run(write: bool, json_path: str | None):
    c = MintsoftClient()
    print("Authenticating + harvesting sample IDs...")
    ids = harvest(c)
    print(f"  harvested: {', '.join(f'{k}={v}' for k, v in ids.items())}\n")

    rows = []
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0, "MANUAL": 0}

    for method, path, tier, params, note in REG:
        if path == "Auth":
            rows.append(("PASS", method, path, tier, 200, "client authed", note))
            counts["PASS"] += 1
            continue

        if tier in ("A", "B"):
            filled_path = _fill(path, ids)
            filled_params = _fill(params, ids) if params else None
            if filled_path is None or (params and filled_params is None):
                rows.append(("SKIP", method, path, tier, None, "no sample id to fill", note))
                counts["SKIP"] += 1
                continue
            res: Result = c.request(method, filled_path, params=filled_params)
            status = "PASS" if res.ok else "FAIL"
            counts[status] += 1
            rows.append((status, method, path, tier, res.status, res.summary, note))
        else:
            # C/D: never auto-fire. The guarded write demo (below) covers a safe subset.
            rows.append(("MANUAL", method, path, tier, None, "needs test fixture / --write", note))
            counts["MANUAL"] += 1

    # ---- guarded write demo, test client only ----
    if write:
        print("\n--- WRITE demo (test client, self-cleaning) ---")
        _write_demo(c, ids, rows, counts)

    _report(rows, counts)
    if json_path:
        with open(json_path, "w") as f:
            json.dump([dict(zip(
                ["result", "method", "path", "tier", "status", "summary", "note"], r))
                for r in rows], f, indent=2)
        print(f"\nJSON written to {json_path}")


def _write_demo(c, ids, rows, counts):
    """Create a throwaway product on the test client, read it back, delete it.

    Proves PUT/GET/DELETE Product on a safe target without touching real data.
    Extend cautiously — every call here MUST be scoped to test_client_id.
    """
    tcid = ids.get("test_client_id")
    if not tcid:
        print("  no test client id found; skipping write demo")
        return
    sku = f"SMOKE-TEST-{TODAY.strftime('%Y%m%d')}"
    payload = {"ClientId": tcid, "SKU": sku, "Name": "Smoke test product (delete me)"}
    create = c.request("PUT", "Product", json=payload)
    rows.append(("PASS" if create.ok else "FAIL", "PUT", "Product", "D",
                 create.status, create.summary, "write demo: create on test client"))
    counts["PASS" if create.ok else "FAIL"] += 1
    new_id = _first_id(create.data) if isinstance(create.data, dict) else None
    if new_id:
        got = c.request("GET", f"Product/{new_id}")
        rows.append(("PASS" if got.ok else "FAIL", "GET", "Product/{product_id}", "B",
                     got.status, got.summary, "write demo: read back"))
        counts["PASS" if got.ok else "FAIL"] += 1
        dele = c.request("DELETE", f"Product/{new_id}")
        rows.append(("PASS" if dele.ok else "FAIL", "DELETE", "Product/{product_id}", "D",
                     dele.status, dele.summary, "write demo: cleanup"))
        counts["PASS" if dele.ok else "FAIL"] += 1
        print(f"  created+read+deleted product {new_id} ({sku}) on client {tcid}")
    else:
        print("  create did not return an id; nothing to clean up")


def _report(rows, counts):
    icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⚪", "MANUAL": "🟡"}
    print("\n" + "=" * 96)
    print(f"{'':2} {'M':6} {'PATH':52} {'TIER':4} {'HTTP':5} SUMMARY")
    print("-" * 96)
    for result, method, path, tier, status, summary, note in rows:
        # only print the interesting lines in full; keep it scannable
        line = f"{icon[result]} {method:6} {path:52.52} {tier:4} {str(status or ''):5} {summary[:28]}"
        print(line)
    print("=" * 96)
    total = sum(counts.values())
    print(f"TOTAL {total}  |  ✅ {counts['PASS']} PASS   "
          f"❌ {counts['FAIL']} FAIL   ⚪ {counts['SKIP']} SKIP   🟡 {counts['MANUAL']} MANUAL")
    print("A+B run live (read-only). C/D are MANUAL unless exercised via --write on the test client.")


if __name__ == "__main__":
    run(write="--write" in sys.argv,
        json_path=next((a.split("=", 1)[1] if "=" in a else sys.argv[sys.argv.index(a) + 1]
                        for a in sys.argv if a.startswith("--json")), None))
