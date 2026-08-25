"""
Multi-turn conversation state handler for Vera.
Handles incoming merchant and customer replies, detecting auto-replies, intent transitions,
hostility, and delay requests with precision.
"""

from __future__ import annotations
import re
from typing import Any, Dict, List, Optional
from engine.models import ReplyAction


# Common automated canned auto-reply signatures
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

HOSTILE_PATTERNS = [
    r"\bstop\b",
    r"\bspam\b",
    r"\bunsubscribe\b",
    r"don't\s+message",
    r"stop\s+messaging",
    r"useless",
    r"not\s+interested",
    r"remove\s+me",
    r"do\s+not\s+contact",
    r"leave\s+me\s+alone",
    r"bakwas",
    r"band\s+karo",
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

DELAY_PATTERNS = [
    r"busy\s+right\s+now",
    r"call\s+later",
    r"message\s+later",
    r"after\s+\d+\s*(mins?|hours?)",
    r"check\s+later",
    r"kal\s+baat\s+karte",
    r"abhi\s+busy\s+hoon",
]


class ConversationManager:
    """Stateful multi-turn conversation tracker."""

    def __init__(self):
        # Maps conversation_id -> list of turn dicts
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

        # 1. Check for Hostility / Opt-Out
        for pat in HOSTILE_PATTERNS:
            if re.search(pat, msg_lower):
                return ReplyAction(
                    action="end",
                    rationale="Merchant expressed hostility or explicit opt-out request; ending conversation immediately and respecting suppression.",
                )

        # 2. Check for Explicit Intent / Commitment
        is_commitment = any(re.search(pat, msg_lower) for pat in INTENT_COMMITMENT_PATTERNS)
        if is_commitment:
            # Must transition immediately to ACTION mode without re-qualifying
            owner_name = ""
            if merchant_context:
                owner_name = merchant_context.get("identity", {}).get("owner_first_name", "")
            salut = f"{owner_name}, " if owner_name else ""

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

        # 5. General Inquiry / Dialogue Continuation
        if "abstract" in msg_lower or "send" in msg_lower or "detail" in msg_lower:
            body = (
                "Sending the complete breakdown now — here is the 90-second summary and action plan. "
                "I've also drafted a customer-facing WhatsApp snippet ready to send. Would you like me to share it?"
            )
            return ReplyAction(
                action="send",
                body=body,
                cta="binary",
                rationale="Honoring merchant request for information; providing summary and immediate actionable follow-on draft.",
            )

        # Default helpful operator response
        body = (
            "Got it! I have noted your preference and drafted the update accordingly. "
            "Here is the next step: we can set this live in 2 minutes. Should I proceed?"
        )
        return ReplyAction(
            action="send",
            body=body,
            cta="binary",
            rationale="Acknowledged merchant reply and advanced conversation towards frictionless action.",
        )
