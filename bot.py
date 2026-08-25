"""
magicpin AI Challenge — Vera Message Engine (FastAPI Server)
============================================================
Exposes all 5 endpoints required by the challenge contract:
1. GET /v1/healthz
2. GET /v1/metadata
3. POST /v1/context
4. POST /v1/tick
5. POST /v1/reply
"""

from __future__ import annotations
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

from engine.models import (
    ContextPushRequest,
    ContextPushResponse,
    TickRequest,
    TickResponse,
    ActionItem,
    ReplyRequest,
    ReplyResponse,
    HealthzResponse,
    MetadataResponse,
)
from engine.composer import compose
from engine.reply_handler import ConversationManager

app = FastAPI(
    title="Vera Message Engine",
    description="magicpin AI Challenge — Merchant Growth Message Engine",
    version="1.0.0",
)

START_TIME = time.time()

# In-memory context storage: (scope, context_id) -> {"version": int, "payload": dict}
context_store: Dict[tuple[str, str], Dict[str, Any]] = {}
conversation_manager = ConversationManager()


@app.get("/v1/healthz", response_model=HealthzResponse)
async def healthz():
    """Liveness and readiness probe reporting loaded context counts."""
    counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    for (scope, _), _ in context_store.items():
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
        version="1.0.0",
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
    Inspects available triggers and generates proactive outreach messages.
    """
    actions: List[ActionItem] = []

    for trg_id in body.available_triggers:
        trg_entry = context_store.get(("trigger", trg_id))
        if not trg_entry:
            continue
        trg_payload = trg_entry.get("payload", {})

        merchant_id = trg_payload.get("merchant_id") or trg_payload.get("payload", {}).get("merchant_id")
        if not merchant_id:
            # Fallback search in all merchants if not explicit in trigger
            for (s, mid), mdata in context_store.items():
                if s == "merchant":
                    merchant_id = mid
                    break

        merchant_entry = context_store.get(("merchant", merchant_id)) if merchant_id else None
        if not merchant_entry:
            continue
        merchant = merchant_entry.get("payload", {})

        cat_slug = merchant.get("category_slug", "")
        category_entry = context_store.get(("category", cat_slug))
        category = category_entry.get("payload", {}) if category_entry else {"slug": cat_slug}

        customer_id = trg_payload.get("customer_id")
        customer = None
        if customer_id:
            cx_entry = context_store.get(("customer", customer_id))
            if cx_entry:
                customer = cx_entry.get("payload")

        # Compose message
        composed = compose(
            category=category,
            merchant=merchant,
            trigger=trg_payload,
            customer=customer,
        )

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
    Handles auto-reply detection, immediate intent transition to action, hostility, and delays.
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
    uvicorn.run("bot:app", host="0.0.0.0", port=8080, reload=True)
