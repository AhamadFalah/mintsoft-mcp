"""Reusable Mintsoft bundle helper.

`amend_bundle()` changes a bundle's component make-up. The Mintsoft API has no
in-place bundle edit, so this does **delete + recreate** — which gives the bundle a
NEW product id, losing anything tied to the old id (alt codes / channel mappings,
order-history links, order rules).

SAFETY: it REFUSES by default if the bundle has real alt codes or recent stock
activity, so it can't silently nuke mappings. Pass force=True to override (you
accept the loss), or amend such bundles in the Mintsoft UI (which keeps the id).
If the recreate fails after the delete, it rolls back to the original components.
"""
from __future__ import annotations
from datetime import date, timedelta

from .mintsoft_client import MintsoftClient, write_allowlist  # noqa: F401  (re-exported for callers)

JUNK_ALT_TYPES = {"UNLEASHED_ Product_Guid", "Unleashed-NDP"}
WRITE_CLIENTS = write_allowlist()   # writes only on allowlisted clients (MINTSOFT_WRITE_CLIENT_ALLOWLIST)


def real_alt_codes(c, pid):
    """Channel alt codes, excluding the Unleashed GUID / 'False' junk."""
    out = []
    for a in c.get(f"Product/{pid}/AltCodes").data or []:
        if isinstance(a, dict):
            s, t = a.get("SKU"), a.get("Type")
            if t in JUNK_ALT_TYPES or s in (None, "False", ""):
                continue
            out.append({"SKU": s, "Type": t})
    return out


def recent_stock_flow(c, pid, days):
    to_d = date.today().isoformat()
    from_d = (date.today() - timedelta(days=days)).isoformat()
    fl = c.get(f"Product/{pid}/StockFlow", {"FromDate": from_d, "ToDate": to_d}).data
    return fl if isinstance(fl, list) else []


def bundle_components(c, pid):
    b = c.get(f"Product/{pid}/Bundle").data or {}
    return [{"ProductId": x.get("ProductId"), "Quantity": x.get("Quantity")}
            for x in (b.get("Components") or []) if isinstance(x, dict)]


def resolve_bundle_pid(c, client_id, sku, max_pages=60):
    """Find a bundle's product id by SKU within a client (Product/Search hides bundles)."""
    for page in range(1, max_pages + 1):
        rows = c.get("Product/List", {"ClientId": client_id, "PageNo": page, "Limit": 100}).data or []
        if not rows:
            break
        for p in rows:
            if isinstance(p, dict) and p.get("SKU") == sku and p.get("Bundle"):
                return p.get("ID")
        if len(rows) < 100:
            break
    return None


def amend_bundle(c, new_components, *, product_id=None, sku=None, client_id=None,
                 new_name=None, new_description=None, force=False, allow_live=False, recent_days=90):
    """Edit a bundle (components, and optionally name/description) via delete + recreate.

    Identify the bundle by `product_id` OR (`sku` + `client_id`).
    `new_components` = [{"ProductId": int, "Quantity": int}, ...].
    `new_name`/`new_description` (optional) update the bundle's name/description; if only `new_name`
    is given, the description mirrors it.
    `allow_live=True` permits writing to a non-allowlisted client (explicit override of the read-only
    policy). `force=True` overrides the alt-codes / recent-stock safety block.
    Returns {ok, ...} on success, or {ok:False, refused/live_readonly/error, ...}.
    """
    # --- resolve ---
    pid = product_id
    if pid is None:
        if not (sku and client_id):
            return {"ok": False, "error": "pass product_id, or sku + client_id"}
        pid = resolve_bundle_pid(c, client_id, sku)
        if not pid:
            return {"ok": False, "error": f"bundle SKU {sku!r} not found for client {client_id}"}

    prod = c.get(f"Product/{pid}").data
    if not isinstance(prod, dict):
        return {"ok": False, "error": f"product {pid} not found"}
    if not prod.get("Bundle"):
        return {"ok": False, "error": f"product {pid} ({prod.get('SKU')!r}) is not a bundle"}
    sku, cid = prod.get("SKU"), prod.get("ClientId")
    name, pack = prod.get("Name"), prod.get("PackingInstructions")

    # --- LIVE-CLIENT GUARD (writes only on allowlisted clients) ---
    if cid not in WRITE_CLIENTS and not allow_live:
        return {"ok": False, "live_readonly": True, "pid": pid, "sku": sku, "client_id": cid,
                "hint": "Client is not in the write allowlist. Re-run with allow_live=True to edit it via "
                        "the API (delete+recreate, new ProductId), or amend it in the Mintsoft UI to keep the id."}

    # --- SAFETY GUARD (before any destructive call) ---
    alts = real_alt_codes(c, pid)
    flows = recent_stock_flow(c, pid, recent_days)
    blockers = []
    if alts:
        blockers.append(f"{len(alts)} alt code(s): {[a['SKU'] for a in alts]}")
    if flows:
        blockers.append(f"{len(flows)} stock-flow record(s) in last {recent_days}d")
    if blockers and not force:
        return {"ok": False, "refused": True, "pid": pid, "sku": sku, "blockers": blockers,
                "hint": "recreate makes a NEW id and loses these. Amend in the Mintsoft UI to keep the id, "
                        "or pass force=True to override."}

    # --- capture originals for rollback, then delete + recreate ---
    original = bundle_components(c, pid)
    effective_name = new_name if new_name is not None else name
    d = c.request("DELETE", f"Product/{pid}")
    if not (isinstance(d.data, dict) and d.data.get("Success")):
        return {"ok": False, "error": f"delete failed (nothing changed): {d.status} {d.summary[:120]}"}

    payload = {"SKU": sku, "Name": effective_name, "PackingInstructions": pack,
               "ClientId": cid, "Components": new_components}
    r = c.request("PUT", "Product/Bundle", json=payload)
    rd = r.data if isinstance(r.data, dict) else {}
    if not rd.get("Success"):
        # rollback to original name + components (new id, but no data dropped)
        rb = c.request("PUT", "Product/Bundle", json={"SKU": sku, "Name": name,
                       "PackingInstructions": pack, "ClientId": cid, "Components": original})
        return {"ok": False, "error": f"recreate failed: {rd.get('Message')}",
                "rolled_back": bool((rb.data or {}).get("Success")),
                "rollback_pid": (rb.data or {}).get("ProductId")}

    new_pid = rd.get("ProductId")
    # bundle-create can't set Description — apply name/description via a product update if requested
    desc = None
    if new_description is not None or new_name is not None:
        desc = new_description if new_description is not None else effective_name
        c.request("POST", "Product", json={"ID": new_pid, "ClientId": cid, "SKU": sku,
                                            "Name": effective_name, "Description": desc})
    return {"ok": True, "old_pid": pid, "new_pid": new_pid, "sku": sku,
            "name": effective_name, "description": desc, "live": cid not in WRITE_CLIENTS,
            "components": bundle_components(c, new_pid), "forced": force,
            "note": "NEW product id — alt codes / order-history on the old id are gone"}
