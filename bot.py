"""
Vera Message Engine — FastAPI Server.
Implements the full magicpin Challenge contract:
- GET /v1/healthz
- GET /v1/metadata
- POST /v1/context
- POST /v1/tick
- POST /v1/reply
"""

from __future__ import annotations
import os
import time
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from engine.models import (
    ContextPushRequest,
    ContextPushResponse,
    TickRequest,
    TickResponse,
    ReplyRequest,
    ReplyResponse,
    HealthzResponse,
    MetadataResponse,
    ActionItem,
)
from engine.composer import compose
from engine.reply_handler import ConversationManager


app = FastAPI(
    title="Vera Message Engine",
    version="1.0.0",
    description="Engine behind Vera — magicpin's retailer AI for hyper-local merchant and customer engagement.",
)

# Global in-memory storage
START_TIME = time.time()
context_store: Dict[Tuple[str, str], Dict[str, Any]] = {}
sent_suppression_keys: Set[str] = set()
conversation_manager = ConversationManager()


def _preload_dataset():
    """Seed base dataset into context_store on server startup."""
    base_dir = Path(__file__).parent / "expanded"
    if not base_dir.exists():
        base_dir = Path(__file__).parent / "dataset"
        if not base_dir.exists():
            return

    # 1. Categories
    cat_dir = base_dir / "categories"
    if cat_dir.exists():
        for p in cat_dir.glob("*.json"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    slug = data.get("slug") or p.stem
                    context_store[("category", slug)] = {
                        "version": 1,
                        "payload": data,
                        "delivered_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    }
            except Exception:
                pass

    # 2. Merchants
    m_dir = base_dir / "merchants"
    if m_dir.exists():
        for p in m_dir.glob("*.json"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    mid = data.get("merchant_id") or p.stem
                    context_store[("merchant", mid)] = {
                        "version": 1,
                        "payload": data,
                        "delivered_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    }
            except Exception:
                pass


_preload_dataset()


@app.get("/", response_class=HTMLResponse)
async def root():
    """Interactive dashboard for the Vera Message Engine."""
    uptime = int(time.time() - START_TIME)
    counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    for scope, _ in context_store.keys():
        counts[scope] = counts.get(scope, 0) + 1

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Vera Message Engine — magicpin AI Challenge</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #0f172a; color: #f8fafc; padding: 2rem; }}
    .card {{ background: #1e293b; padding: 1.5rem; border-radius: 12px; border: 1px solid #334155; max-width: 600px; margin: 0 auto; }}
    h1 {{ color: #38bdf8; font-size: 1.5rem; margin-top: 0; }}
    .status {{ color: #34d399; font-weight: bold; }}
    ul {{ line-height: 1.8; }}
    a {{ color: #818cf8; text-decoration: none; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Vera Message Engine</h1>
    <p>Status: <span class="status">LIVE & HEALTHY</span></p>
    <p>Uptime: {uptime} seconds | Active Suppressions: {len(sent_suppression_keys)}</p>
    <p>Contexts Loaded: {counts}</p>
    <ul>
      <li><a href="/v1/healthz">GET /v1/healthz</a></li>
      <li><a href="/v1/metadata">GET /v1/metadata</a></li>
      <li><a href="/docs">Swagger API Documentation (/docs)</a></li>
    </ul>
  </div>
</body>
</html>"""


@app.get("/v1/healthz", response_model=HealthzResponse)
async def healthz():
    """Healthcheck endpoint reporting uptime and loaded contexts."""
    counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    for scope, _ in context_store.keys():
        counts[scope] = counts.get(scope, 0) + 1

    return HealthzResponse(
        status="ok",
        uptime_seconds=int(time.time() - START_TIME),
        contexts_loaded=counts,
    )


@app.get("/v1/metadata", response_model=MetadataResponse)
async def metadata():
    """Returns bot identity, model details, and architecture approach."""
    return MetadataResponse(
        team_name="Team Vera",
        team_members=["Sanskar Sinha"],
        model="deterministic-grounded-composer-v1",
        approach="4-context deterministic synthesis engine with grounded entity extraction & multi-turn intent state machine",
        contact_email="sanskar@example.com",
        version="1.0.1-ed6c44a",
        submitted_at="2026-04-26T08:00:00Z",
    )


@app.post("/v1/context")
async def push_context(body: ContextPushRequest):
    """
    Idempotent context push endpoint.
    Higher version replaces atomically. Same version is an idempotent no-op. Strictly lower version returns 409.
    """
    key = (body.scope, body.context_id)
    cur = context_store.get(key)

    if cur and cur.get("version", 0) > body.version:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "accepted": False,
                "reason": "stale_version",
                "current_version": cur.get("version", 0),
            },
        )

    context_store[key] = {
        "version": body.version,
        "payload": body.payload,
        "delivered_at": body.delivered_at,
    }

    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "accepted": True,
        "ack_id": f"ack_{body.context_id}_v{body.version}",
        "stored_at": now_iso,
    }


@app.post("/v1/tick", response_model=TickResponse)
async def tick(body: TickRequest):
    """
    Periodic wake-up endpoint.
    Inspects available triggers, prioritizes by urgency, suppresses duplicates, and generates proactive messages.
    """
    actions: List[ActionItem] = []

    # 1. Resolve trigger payloads
    resolved_triggers = []
    for trg_id in body.available_triggers:
        trg_entry = context_store.get(("trigger", trg_id))
        trg_payload = trg_entry.get("payload", {}) if trg_entry else None

        if not trg_payload:
            # Fallback to local files if not pushed dynamically
            trg_file = Path("expanded/triggers") / f"{trg_id}.json"
            if trg_file.exists():
                try:
                    with open(trg_file, "r", encoding="utf-8") as f:
                        trg_payload = json.load(f)
                except Exception:
                    pass
            if not trg_payload:
                matches = list(Path("expanded/triggers").glob(f"*{trg_id}*.json"))
                if matches:
                    try:
                        with open(matches[0], "r", encoding="utf-8") as f:
                            trg_payload = json.load(f)
                    except Exception:
                        pass
            if not trg_payload:
                trg_payload = {"id": trg_id, "kind": trg_id, "payload": {}}

        urgency = trg_payload.get("urgency", 1)
        resolved_triggers.append((urgency, trg_id, trg_payload))

    # 2. Prioritize by Urgency (Highest urgency first, e.g. 5 down to 1)
    resolved_triggers.sort(key=lambda x: x[0], reverse=True)

    # 3. Process triggers and emit actions
    for urgency, trg_id, trg_payload in resolved_triggers:
        if len(actions) >= 20:
            break  # Adhere to 20 actions/tick cap

        merchant_id = trg_payload.get("merchant_id") or trg_payload.get("payload", {}).get("merchant_id")
        customer_id = trg_payload.get("customer_id")
        cat_hint = trg_payload.get("payload", {}).get("category") or trg_payload.get("category_slug") or trg_payload.get("category")

        # Resolve customer if present
        customer = None
        if customer_id:
            cx_entry = context_store.get(("customer", customer_id))
            if not cx_entry:
                cx_file = Path("expanded/customers") / f"{customer_id}.json"
                if cx_file.exists():
                    try:
                        with open(cx_file, "r", encoding="utf-8") as f:
                            cx_entry = {"payload": json.load(f)}
                    except Exception:
                        pass
            if cx_entry:
                customer = cx_entry.get("payload")
                if not merchant_id:
                    merchant_id = customer.get("relationship", {}).get("primary_merchant_id")

        # Resolve merchant
        if not merchant_id and cat_hint:
            for (s, mid), mdata in context_store.items():
                if s == "merchant":
                    mpay = mdata.get("payload", {})
                    if mpay.get("category_slug") == cat_hint:
                        merchant_id = mid
                        break

        merchant_entry = context_store.get(("merchant", merchant_id)) if merchant_id else None
        if not merchant_entry and merchant_id:
            m_file = Path("expanded/merchants") / f"{merchant_id}.json"
            if m_file.exists():
                try:
                    with open(m_file, "r", encoding="utf-8") as f:
                        merchant_entry = {"payload": json.load(f)}
                except Exception:
                    pass

        if not merchant_entry:
            # If no merchant can be identified, skip this trigger to avoid sending to wrong merchant
            continue

        merchant = merchant_entry.get("payload", {})
        cat_slug = merchant.get("category_slug") or cat_hint or "generic"

        # Resolve category
        category_entry = context_store.get(("category", cat_slug))
        if not category_entry:
            c_file = Path("expanded/categories") / f"{cat_slug}.json"
            if c_file.exists():
                try:
                    with open(c_file, "r", encoding="utf-8") as f:
                        category_entry = {"payload": json.load(f)}
                except Exception:
                    pass
        category = category_entry.get("payload", {}) if category_entry else {"slug": cat_slug}

        # Compose message
        composed = compose(
            category=category,
            merchant=merchant,
            trigger=trg_payload,
            customer=customer,
        )

        # 4. Enforce Suppression Key (Spam prevention across ticks)
        if composed.suppression_key in sent_suppression_keys:
            continue
        sent_suppression_keys.add(composed.suppression_key)

        conv_id = f"conv_{merchant_id}_{trg_id}"
        actions.append(
            ActionItem(
                conversation_id=conv_id,
                merchant_id=merchant_id,
                customer_id=customer_id,
                send_as=composed.send_as,
                trigger_id=trg_id,
                template_name=composed.template_name,
                template_params=composed.template_params,
                body=composed.body,
                cta=composed.cta,
                suppression_key=composed.suppression_key,
                rationale=composed.rationale,
            )
        )

    return TickResponse(actions=actions)


@app.post("/v1/reply", response_model=ReplyResponse)
async def reply(body: ReplyRequest):
    """
    Receives merchant / customer reply and decides next conversational move.
    Handles auto-reply detection, immediate intent transition to action, hostility, pricing, and delays.
    """
    m_entry = context_store.get(("merchant", body.merchant_id)) if body.merchant_id else None
    merchant_ctx = m_entry.get("payload") if m_entry else None

    cat_slug = merchant_ctx.get("category_slug", "") if merchant_ctx else ""
    cat_entry = context_store.get(("category", cat_slug)) if cat_slug else None
    cat_ctx = cat_entry.get("payload") if cat_entry else None

    result = conversation_manager.handle_reply(
        conversation_id=body.conversation_id,
        merchant_id=body.merchant_id,
        customer_id=body.customer_id,
        from_role=body.from_role,
        message=body.message,
        turn_number=body.turn_number,
        merchant_context=merchant_ctx,
        category_context=cat_ctx,
    )

    return ReplyResponse(
        action=result.action,
        body=result.body,
        cta=result.cta,
        rationale=result.rationale,
        wait_seconds=result.wait_seconds,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bot:app", host="127.0.0.1", port=8000, reload=True)
