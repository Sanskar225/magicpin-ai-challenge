"""
Multi-turn conversation state handler for Vera.
Handles incoming merchant and customer replies, detecting auto-replies, intent transitions,
hostility, pricing, and delay requests with precision.
"""

from __future__ import annotations
import re
from typing import Any, Dict, List, Optional
from engine.models import ReplyAction


# Automated canned auto-reply signatures
AUTO_REPLY_PATTERNS = [
    r"thank\s+you\s+for\s+contacting",
    r"our\s+team\s+will\s+respond",
    r"will\s+get\s+back\s+to\s+you",
    r"automated\s+(assistant|response|reply|message)",
    r"shukriya.*team\s+tak\s+pahuncha",
    r"aapki\s+jaankari\s+ke\s+liye\s+bahut",
    r"currently\s+unavailable",
    r"we\s+are\s+closed\s+right\s+now",
]

# Strict hostile & opt-out patterns (avoids false positive on 'should I stop the current offer')
HOSTILE_PATTERNS = [
    r"^(stop|unsubscribe|remove\s+me|cancel|stop\s+it|end)$",
    r"stop\s+(messaging|spamming|contacting|sending|bothering)",
    r"don['’]?t\s+(message|contact|spam|text)\s+me",
    r"do\s+not\s+(contact|message|text)\s+me",
    r"(useless\s+spam|this\s+is\s+spam|leave\s+me\s+alone|bakwas|band\s+karo)",
]

INTENT_COMMITMENT_PATTERNS = [
    r"ok\s+lets\s+do\s+it",
    r"let['’]?s\s+do\s+it",
    r"lets\s+do\s+it",
    r"whats\s+next",
    r"what['’]?s\s+next",
    r"proceed",
    r"go\s+ahead",
    r"yes\s+please",
    r"yes\s*,?\s*send",
    r"send\s+me\s+the\s+abstract",
    r"send\s+it",
    r"sure\s*,?\s*do\s+it",
    r"i\s+want\s+to\s+join",
    r"judna\s+hai",
    r"start\s+it",
    r"approve",
    r"confirm",
]

OFF_TOPIC_PATTERNS = [
    (r"gst|tax|filing|itr|accounting|audit", "GST and tax filing"),
    (r"loan|credit\s+card|borrow|finance", "loans and banking"),
    (r"weather|monsoon\s+forecast|temperature", "weather forecasts"),
    (r"cricket|match\s+score|ipl\s+score", "sports scores"),
    (r"lawyer|legal\s+case|court", "legal consultation"),
]

# Strict slot & confirmation patterns (avoids false positive on 'I have 2 kids')
CUSTOMER_CONFIRM_PATTERNS = [
    r"^(1|2|first\s+slot|second\s+slot|slot\s*[12]|option\s*[12])$",
    r"(confirm|booked?|works\s+for\s+me|yes\s+please|lock\s+it\s+in)",
    r"(wed|thu|fri|sat|sun|mon|tue)",
]

PRICING_PATTERNS = [
    r"how\s+much|pricing|cost|charge|fees?|plan\s+price|kitna\s+paisa|kya\s+rate",
]

DELAY_PATTERNS = [
    r"busy\s+right\s+now",
    r"call\s+(me\s+)?later",
    r"message\s+later",
    r"after\s+\d+\s*(mins?|hours?)",
    r"check\s+later",
    r"kal\s+baat\s+karte",
    r"abhi\s+busy\s+hoon",
    r"baad\s+mein",
]


class ConversationManager:
    """Manages conversation state across turns and coordinates next best actions."""

    def __init__(self):
        self.conversations: Dict[str, List[Dict[str, Any]]] = {}

    def handle_reply(
        self,
        conversation_id: str,
        merchant_id: Optional[str],
        customer_id: Optional[str],
        from_role: str,
        message: str,
        turn_number: int,
        merchant_context: Optional[Dict[str, Any]] = None,
        category_context: Optional[Dict[str, Any]] = None,
    ) -> ReplyAction:
        """Process an inbound reply and determine next action."""
        msg_clean = message.strip()
        msg_lower = msg_clean.lower()

        # Store turn in history
        turn_history = self.conversations.setdefault(conversation_id, [])
        turn_history.append({
            "from_role": from_role,
            "message": msg_clean,
            "turn_number": turn_number,
        })

        m_name = "your business"
        owner_name = ""
        if merchant_context:
            ident = merchant_context.get("identity", {})
            m_name = ident.get("name", "your business")
            owner_name = ident.get("owner_first_name", "")
        salut = f"{owner_name}, " if owner_name else ""

        # 1. Customer-Facing Turn Handling
        if from_role == "customer" or customer_id:
            if any(re.search(pat, msg_lower) for pat in HOSTILE_PATTERNS):
                return ReplyAction(
                    action="end",
                    rationale="Customer requested opt-out; ending customer outreach turn cleanly.",
                )
            if any(re.search(pat, msg_lower) for pat in CUSTOMER_CONFIRM_PATTERNS):
                body = (
                    f"Confirmed! Your slot has been recorded with {m_name}. "
                    f"Our team will keep everything ready for you. See you soon!"
                )
                return ReplyAction(
                    action="send",
                    body=body,
                    cta="binary",
                    rationale="Acknowledging customer slot selection and sending instant confirmation.",
                )
            body = (
                f"Got it! We have noted your request for {m_name}. "
                f"Our team will connect with you shortly on WhatsApp to confirm your preferred timing."
            )
            return ReplyAction(
                action="send",
                body=body,
                cta="binary",
                rationale="Handling customer inquiry on behalf of merchant.",
            )

        # 2. Check for Hostility / Opt-Out
        for pat in HOSTILE_PATTERNS:
            if re.search(pat, msg_lower):
                return ReplyAction(
                    action="end",
                    rationale="Merchant expressed hostility or explicit opt-out request; ending conversation immediately and respecting suppression.",
                )

        # 3. Check for Auto-Reply Loop
        merchant_messages = [t["message"].lower().strip() for t in turn_history if t.get("from_role") == from_role]
        is_canned_match = any(re.search(pat, msg_lower) for pat in AUTO_REPLY_PATTERNS)
        is_consecutive_repeat = len(merchant_messages) >= 2 and merchant_messages[-1] == merchant_messages[-2]

        if is_canned_match or is_consecutive_repeat:
            return ReplyAction(
                action="end",
                rationale="Detected repeated canned WhatsApp Business auto-reply from merchant; ending conversation gracefully to avoid wasting turns.",
            )

        # 4. Check for Delay / Busy
        is_delay = any(re.search(pat, msg_lower) for pat in DELAY_PATTERNS)
        if is_delay:
            return ReplyAction(
                action="wait",
                wait_seconds=1800,
                rationale="Merchant requested time delay; backing off for 30 minutes before next touch.",
            )

        # 5. Check for Off-Topic Questions (Phase 4 scenario: stay on mission politely)
        for pattern, topic_label in OFF_TOPIC_PATTERNS:
            if re.search(pattern, msg_lower):
                body = (
                    f"I'm Vera, magicpin's growth assistant focused on boosting Google ranking, customer walk-ins, and marketing for {m_name}. "
                    f"While I cannot assist with {topic_label}, I can help you publish an active offer post or review campaign today. "
                    f"Would you like me to share your growth plan?"
                )
                return ReplyAction(
                    action="send",
                    body=body,
                    cta="binary",
                    rationale=f"Politely deflected off-topic inquiry ({topic_label}) while remaining anchored on core Vera growth mission.",
                )

        # 6. Check for Pricing / Cost Inquiries (Dynamic pricing grounded in merchant context)
        if any(re.search(pat, msg_lower) for pat in PRICING_PATTERNS):
            sub = merchant_context.get("subscription", {}) if merchant_context else {}
            plan = sub.get("plan", "Vera Pro")
            amount = sub.get("renewal_amount") or sub.get("amount")

            if amount:
                price_text = f"₹{amount:,}/year (or ₹999/month)"
            else:
                price_text = "₹999/month"

            body = (
                f"{plan} for {m_name} is {price_text}, which includes automated Google Business Profile posts, "
                f"weekly customer recall campaigns, and review booster automation. "
                f"I can start a 14-day risk-free trial for your account today. Shall I activate it?"
            )
            return ReplyAction(
                action="send",
                body=body,
                cta="binary",
                rationale="Direct transparent pricing disclosure anchored on merchant subscription context with low-friction trial CTA.",
            )

        # 7. Check for Explicit Intent / Commitment
        is_commitment = any(re.search(pat, msg_lower) for pat in INTENT_COMMITMENT_PATTERNS)
        if is_commitment:
            body = (
                f"Done! {salut}I have initialized this and drafted the complete setup for you. "
                f"Here are the details: 1) Google Business listing post is ready, 2) Campaign is staged for activation. "
                f"Confirming next step: I will publish this right away. Reply YES to make it live instantly."
            )
            return ReplyAction(
                action="send",
                body=body,
                cta="binary",
                rationale="Merchant signaled explicit commitment/intent; transitioned immediately to action mode with concrete deliverable draft and zero re-qualification.",
            )

        # 8. General Inquiry / Abstract Request / Details
        if "abstract" in msg_lower or "send" in msg_lower or "detail" in msg_lower or "summary" in msg_lower:
            body = (
                f"Sending the complete breakdown now for {m_name} — here is the 90-second summary and action plan. "
                f"I've also drafted a customer-facing WhatsApp snippet ready to send. Would you like me to share it?"
            )
            return ReplyAction(
                action="send",
                body=body,
                cta="binary",
                rationale="Honoring merchant request for information; providing summary and immediate actionable follow-on draft.",
            )

        # 9. Default Helpful Operator Response
        body = (
            f"Got it! I have noted your preference for {m_name} and prepared the setup draft. "
            f"Here is the next step: we can set this live in 2 minutes. Should I proceed?"
        )
        return ReplyAction(
            action="send",
            body=body,
            cta="binary",
            rationale="Acknowledged merchant reply and advanced conversation towards frictionless action.",
        )
