"""Discontinue-SKU tooling for Mintsoft.

precheck_discontinue()  -- READ-ONLY footprint; safe on ANY client. Surfaces
   blockers (stock, live-order allocations, inbound/backorder, bundle membership)
   and info (alt codes, replen points).
discontinue_sku()       -- the action: POST rename SKU+Name to '[Discontinued] …'
   and set DisCont=true. WRITES ONLY on allowlisted clients; on any other client
   it returns the footprint and tells you to do it in the Mintsoft UI instead.
   Refuses if there are unresolved blockers unless force=True.

'Live order' = allocated stock (orders not yet DESPATCHED/INVOICED/CANCELLED reserve
stock; that shows as Inventory.Allocated). Rename keeps the SAME ProductId, so alt
codes / bundle links / history are preserved.
"""
from __future__ import annotations

from .mintsoft_client import MintsoftClient, write_allowlist  # noqa: F401

WRITE_CLIENTS = write_allowlist()         # allowlisted clients only; everything else read-only
JUNK_ALT_TYPES = {"UNLEASHED_ Product_Guid", "Unleashed-NDP"}
DISC_PREFIX = "[Discontinued] "


def _resolve(c, sku, product_id, client_id):
    if product_id:
        return product_id
    lk = c.get("Product/LookupProductId", {"SKU": sku}).data
    if isinstance(lk, int) and lk:
        return lk
    if client_id:                          # fall back to a client scan (catches bundles too)
        for page in range(1, 60):
            rows = c.get("Product/List", {"ClientId": client_id, "PageNo": page, "Limit": 100}).data or []
            if not rows:
                break
            for p in rows:
                if isinstance(p, dict) and p.get("SKU") == sku:
                    return p.get("ID")
            if len(rows) < 100:
                break
    return None


def _inventory(c, pid):
    recs = c.get(f"Product/{pid}/Inventory").data or []
    t = {"OnHand": 0, "Allocated": 0, "OnOrder": 0, "RequiredByBackOrder": 0, "InQuarantine": 0}
    locs = 0
    for r in recs:
        if isinstance(r, dict):
            locs += 1
            for k in t:
                t[k] += r.get(k) or 0
    return t, locs


def _bundle_memberships(c, client_id, pid):
    out = []
    for page in range(1, 60):
        rows = c.get("Product/List", {"ClientId": client_id, "PageNo": page, "Limit": 100}).data or []
        if not rows:
            break
        for p in rows:
            if isinstance(p, dict) and p.get("Bundle"):
                b = c.get(f"Product/{p['ID']}/Bundle").data or {}
                for comp in (b.get("Components") or []):
                    if isinstance(comp, dict) and comp.get("ProductId") == pid:
                        out.append({"bundle_pid": p.get("ID"), "bundle_sku": p.get("SKU"),
                                    "qty": comp.get("Quantity")})
        if len(rows) < 100:
            break
    return out


def _real_alt_codes(c, pid):
    out = []
    for a in c.get(f"Product/{pid}/AltCodes").data or []:
        if isinstance(a, dict) and a.get("Type") not in JUNK_ALT_TYPES and a.get("SKU") not in (None, "False", ""):
            out.append({"SKU": a.get("SKU"), "Type": a.get("Type")})
    return out


def precheck_discontinue(c, sku=None, *, product_id=None, client_id=None):
    """READ-ONLY footprint + blocker list. Works on any client."""
    pid = _resolve(c, sku, product_id, client_id)
    if not pid:
        return {"found": False, "error": f"product not found (sku={sku!r}, id={product_id})"}
    prod = c.get(f"Product/{pid}").data or {}
    cid = prod.get("ClientId")
    inv, locs = _inventory(c, pid)
    bundles = _bundle_memberships(c, cid, pid) if cid else []
    alts = _real_alt_codes(c, pid)
    replen = [r for r in (c.get("ReplenPoints").data or []) if isinstance(r, dict) and r.get("ProductId") == pid]

    blockers = []
    if inv["OnHand"] > 0:
        blockers.append(f"stock on hand: {inv['OnHand']} (across {locs} location[s])")
    if inv["Allocated"] > 0:
        blockers.append(f"live-order allocations: {inv['Allocated']} unit(s) reserved")
    if inv["OnOrder"] or inv["RequiredByBackOrder"]:
        blockers.append(f"inbound/backorder: OnOrder {inv['OnOrder']}, backorder {inv['RequiredByBackOrder']}")
    if bundles:
        blockers.append(f"component of {len(bundles)} bundle(s): {[b['bundle_sku'] for b in bundles]}")

    info = []
    if alts:
        info.append(f"{len(alts)} channel alt code(s): {[a['SKU'] for a in alts]} (survive the rename; remove in UI if you want them to stop syncing)")
    if replen:
        info.append(f"is a replen point ({len(replen)})")

    return {"found": True, "product_id": pid, "sku": prod.get("SKU"), "name": prod.get("Name"),
            "client_id": cid, "disco_already": prod.get("DisCont"),
            "inventory": inv, "bundles": bundles, "alt_codes": alts,
            "blockers": blockers, "info": info, "clear": not blockers}


def discontinue_sku(c, sku=None, *, product_id=None, client_id=None, force=False):
    """Run the footprint, then (if clear) rename to '[Discontinued] …' + set DisCont.
    WRITES ONLY on allowlisted clients; others get the footprint + a UI instruction."""
    pre = precheck_discontinue(c, sku, product_id=product_id, client_id=client_id)
    if not pre.get("found"):
        return {"ok": False, **pre}
    pid, cid = pre["product_id"], pre["client_id"]
    cur_sku, cur_name = pre["sku"], pre["name"]

    if pre["blockers"] and not force:
        return {"ok": False, "refused": True, "reason": "unresolved blockers", "footprint": pre,
                "hint": "resolve the blockers first, or pass force=True to override"}

    if cid not in WRITE_CLIENTS:
        return {"ok": False, "live_readonly": True, "footprint": pre,
                "action_required": f"LIVE client {cid} is read-only — do the rename + DisCont in the Mintsoft UI: "
                                    f"SKU '{cur_sku}' -> '{DISC_PREFIX}{cur_sku}', tick Discontinued."}

    new_sku = f"{DISC_PREFIX}{cur_sku}" if not str(cur_sku).startswith(DISC_PREFIX) else cur_sku
    new_name = f"{DISC_PREFIX}{cur_name}" if not str(cur_name).startswith(DISC_PREFIX) else cur_name
    r = c.request("POST", "Product", json={"ID": pid, "ClientId": cid, "SKU": new_sku,
                                           "Name": new_name, "DisCont": True})
    rd = r.data if isinstance(r.data, dict) else {}
    if not rd.get("Success"):
        return {"ok": False, "error": f"update failed: {rd.get('Message')}", "footprint": pre}
    after = c.get(f"Product/{pid}").data or {}
    return {"ok": True, "product_id": pid, "old_sku": cur_sku, "new_sku": after.get("SKU"),
            "discont": after.get("DisCont"), "forced": force,
            "note": "same ProductId — alt codes / bundle links / history preserved"}
