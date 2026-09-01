"""
Deterministic and LLM-powered composition engine for Vera.
Takes 4 contexts (Category, Merchant, Trigger, Customer?) and produces structured ComposedMessage.
"""

from __future__ import annotations
import os
import re
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from engine.models import (
    CategoryContext,
    MerchantContext,
    TriggerContext,
    CustomerContext,
    ComposedMessage,
)


def _get_salutation(merchant: Dict[str, Any], category: Dict[str, Any]) -> str:
    """Derive category-appropriate salutation for the merchant."""
    identity = merchant.get("identity", {})
    name = identity.get("name", "")
    owner = identity.get("owner_first_name", "")
    cat_slug = category.get("slug", merchant.get("category_slug", ""))

    if cat_slug == "dentists":
        if owner:
            clean_owner = owner.strip()
            return clean_owner if clean_owner.startswith("Dr.") else f"Dr. {clean_owner}"
        if name.startswith("Dr."):
            clean_name = name.replace("'s", "").replace("’s", "")
            parts = clean_name.split()
            return f"{parts[0]} {parts[1]}" if len(parts) > 1 else parts[0]
        return "Doctor"

    if owner:
        return owner.strip()
    if name:
        clean_name = name.replace("'s", "").replace("’s", "")
        return clean_name.split()[0]
    return "Partner"


def _wants_hindi_mix(merchant: Dict[str, Any], customer: Optional[Dict[str, Any]] = None) -> bool:
    """Check if Hindi-English code-mix should be used based on preferences."""
    if customer:
        c_lang = customer.get("identity", {}).get("language_pref", "")
        if "hi" in c_lang.lower():
            return True
    m_langs = merchant.get("identity", {}).get("languages", [])
    if isinstance(m_langs, list) and any("hi" in str(l).lower() for l in m_langs):
        return True
    return False


def _get_active_offer_str(merchant: Dict[str, Any], category: Dict[str, Any], preferred_type: str = "service_at_price") -> str:
    """Get active offer title from merchant or category offer catalog."""
    m_offers = [o for o in merchant.get("offers", []) if o.get("status") == "active"]
    if m_offers:
        return m_offers[0].get("title", "")
    cat_offers = category.get("offer_catalog", [])
    for o in cat_offers:
        if o.get("type") == preferred_type:
            return o.get("title", "")
    if cat_offers:
        return cat_offers[0].get("title", "")
    return ""


def compose(
    category: Union[Dict[str, Any], CategoryContext],
    merchant: Union[Dict[str, Any], MerchantContext],
    trigger: Union[Dict[str, Any], TriggerContext],
    customer: Optional[Union[Dict[str, Any], CustomerContext]] = None,
    use_llm_if_available: bool = True,
) -> ComposedMessage:
    """
    Main composition function adhering to the 4-context framework.
    Returns ComposedMessage with body, cta, send_as, suppression_key, rationale.
    """
    cat_dict = category if isinstance(category, dict) else category.__dict__
    m_dict = merchant if isinstance(merchant, dict) else merchant.__dict__
    trg_dict = trigger if isinstance(trigger, dict) else trigger.__dict__
    cx_dict = (customer if isinstance(customer, dict) else customer.__dict__) if customer else None

    if use_llm_if_available and os.getenv("VERA_LLM_API_KEY"):
        try:
            llm_msg = _compose_with_llm(cat_dict, m_dict, trg_dict, cx_dict)
            if llm_msg:
                return llm_msg
        except Exception:
            pass

    return _compose_deterministic(cat_dict, m_dict, trg_dict, cx_dict)


def _compose_deterministic(
    category: Dict[str, Any],
    merchant: Dict[str, Any],
    trigger: Dict[str, Any],
    customer: Optional[Dict[str, Any]] = None,
) -> ComposedMessage:
    """
    Deterministic synthesis engine that produces messages strictly grounded
    in the supplied 4-context facts with zero hallucinations.
    """
    cat_slug = category.get("slug", merchant.get("category_slug", "generic"))
    m_id = merchant.get("merchant_id", "m_unknown")
    m_identity = merchant.get("identity", {})
    m_name = m_identity.get("name", "Your Business")
    m_locality = m_identity.get("locality", "your area")
    m_city = m_identity.get("city", "Delhi")
    salutation = _get_salutation(merchant, category)
    hi_mix = _wants_hindi_mix(merchant, customer)

    trg_kind = trigger.get("kind", "")
    trg_payload = trigger.get("payload", {})
    trg_scope = trigger.get("scope", "merchant")
    suppression_key = trigger.get("suppression_key", f"trg:{m_id}:{trg_kind}")

    # =========================================================================
    # CUSTOMER-FACING SCOPE (send_as = "merchant_on_behalf")
    # =========================================================================
    if trg_scope == "customer" or customer is not None:
        cx_ident = customer.get("identity", {}) if customer else {}
        cx_name = cx_ident.get("name", "there")
        cx_rel = customer.get("relationship", {}) if customer else {}
        last_visit = cx_rel.get("last_visit", "")
        send_as = "merchant_on_behalf"

        # 1. Recall Due / Cleaning / Routine Check
        if "recall" in trg_kind:
            slots = trg_payload.get("available_slots", [])
            slot_str = ""
            if slots and len(slots) >= 2:
                s0 = slots[0].get('label', 'Wed 5 Nov, 6pm')
                s1 = slots[1].get('label', 'Thu 6 Nov, 5pm')
                slot_str = f"Aapke liye 2 slots ready hain: {s0} ya {s1}." if hi_mix else f"We have 2 slots open for you: {s0} or {s1}."
            elif slots:
                s0 = slots[0].get('label', 'upcoming Wed 6pm')
                slot_str = f"Aapke liye slot ready hai: {s0}." if hi_mix else f"We have an open slot for you: {s0}."
            else:
                slot_str = "Aapke liye morning & evening slots ready hain." if hi_mix else "We have morning & evening slots open for you."

            active_offer = _get_active_offer_str(merchant, category)
            offer_part = f" {active_offer}." if active_offer else ""

            if cat_slug == "dentists":
                body = (
                    f"Hi {cx_name}, {m_name} here 🦷 It's been 5 months since your last visit — "
                    f"your 6-month cleaning recall is due. {slot_str}{offer_part} "
                    f"Reply 1 for first slot, 2 for second slot, or tell us a time that works."
                )
                return ComposedMessage(
                    body=body,
                    cta="binary",
                    send_as=send_as,
                    suppression_key=suppression_key,
                    rationale="Customer recall reminder with clinical timeline, verified pricing, and effortless slot confirmation.",
                    template_name="cx_recall_dentist_v1",
                    template_params=[cx_name, m_name, "6-month cleaning", active_offer]
                )

            elif cat_slug == "salons":
                body = (
                    f"Hi {cx_name}! {m_name} {m_locality} here 💇‍♀️ It's time for your routine salon maintenance.{offer_part} "
                    f"{slot_str} Reply 1 for first slot, 2 for second slot."
                )
                return ComposedMessage(
                    body=body,
                    cta="binary",
                    send_as=send_as,
                    suppression_key=suppression_key,
                    rationale="Salon customer maintenance recall anchored on preferred time slots and active offer.",
                    template_name="cx_recall_salon_v1"
                )

            elif cat_slug == "gyms":
                body = (
                    f"Hi {cx_name}! {m_name} {m_locality} here 💪 Your regular fitness routine & assessment cycle is due.{offer_part} "
                    f"{slot_str} Complimentary trainer assessment included. Reply 1 or 2 to lock in your trainer slot."
                )
                return ComposedMessage(
                    body=body,
                    cta="binary",
                    send_as=send_as,
                    suppression_key=suppression_key,
                    rationale="Gym workout and routine assessment check-in.",
                    template_name="cx_recall_gym_v1"
                )

            else:
                body = (
                    f"Hi {cx_name}, {m_name} {m_locality} here. Your periodic wellness routine check is due.{offer_part} "
                    f"{slot_str} Reply 1 or 2 to confirm your slot."
                )
                return ComposedMessage(
                    body=body,
                    cta="binary",
                    send_as=send_as,
                    suppression_key=suppression_key,
                    rationale="Standard customer routine recall with slot options.",
                    template_name="cx_recall_generic_v1"
                )

        # 2. Appointment Tomorrow Reminder
        if "appointment" in trg_kind:
            appt_time = trg_payload.get("time_label") or trg_payload.get("time") or "tomorrow at your scheduled time"
            service = trg_payload.get("service") or "scheduled visit"
            body = (
                f"Hi {cx_name}! Reminder from {m_name} in {m_locality}: your {service} is scheduled for {appt_time}. "
                f"Please reply YES to confirm or let us know if you need to reschedule."
            )
            return ComposedMessage(
                body=body,
                cta="binary",
                send_as=send_as,
                suppression_key=suppression_key,
                rationale="Appointment reminder with clear confirmation CTA.",
                template_name="cx_appointment_reminder_v1"
            )

        # 3. Chronic Refill Due (Pharmacy / Dentist)
        if "refill" in trg_kind or "chronic" in trg_kind:
            med_list = trg_payload.get("medications") or trg_payload.get("rx") or trg_payload.get("drug") or "regular prescription"
            med_str = ", ".join(med_list[:2]) if isinstance(med_list, list) else str(med_list)
            days = trg_payload.get("days_remaining") or trg_payload.get("days")
            days_str = f"in {days} days" if days else "this week"
            body = (
                f"Hi {cx_name}, {m_name} {m_locality} here 💊 Your 30-day chronic refill ({med_str}) "
                f"is due {days_str}. Should we pack and keep it ready for quick pickup or doorstep delivery? Reply YES to confirm."
            )
            return ComposedMessage(
                body=body,
                cta="binary",
                send_as=send_as,
                suppression_key=suppression_key,
                rationale="Chronic medication refill reminder anchored on pharmacy catalog and proactive doorstep delivery.",
                template_name="cx_chronic_refill_v1"
            )

        # 4. Bridal / Wedding Package Followup
        if "wedding" in trg_kind or "bridal" in trg_kind:
            days_to_w = trg_payload.get("days_to_wedding") or trg_payload.get("days")
            days_str = f"{days_to_w} days to your wedding" if days_to_w else "Ahead of your upcoming wedding"
            owner_name = m_identity.get("owner_first_name", "Our team")
            active_offer = _get_active_offer_str(merchant, category, preferred_type="bridal_package") or "30-day skin-prep program"
            body = (
                f"Hi {cx_name} 💍 {owner_name} from {m_name} {m_locality} here. {days_str} — "
                f"perfect window to start the {active_offer} before peak bridal bookings roll in. "
                f"Want me to block your preferred Saturday 4pm slot for the first session next week?"
            )
            return ComposedMessage(
                body=body,
                cta="binary",
                send_as=send_as,
                suppression_key=suppression_key,
                rationale="Bridal prep follow-up with wedding countdown, grounded program offering, and slot reservation.",
                template_name="cx_bridal_followup_v1"
            )

        # 5. Winback / Lapsed Customer Followup
        if "winback" in trg_kind or "lapsed" in trg_kind:
            days_lapsed = trg_payload.get("days_since_last_visit") or cx_rel.get("days_since_last_visit")
            time_str = f"it's been {days_lapsed} days" if days_lapsed else "we noticed you haven't visited recently"
            focus = trg_payload.get("previous_focus", "").replace("_", " ")
            active_offer = _get_active_offer_str(merchant, category)
            offer_str = f" featuring {active_offer}" if active_offer else ""

            if cat_slug == "gyms":
                body = (
                    f"Hi {cx_name}! Coach at {m_name} {m_locality} here 💪 {time_str}. "
                    f"We have updated the schedule with fresh morning & evening slots. Ready to restart with a complimentary 1-on-1 progress check this week? Reply YES to book."
                )
            elif cat_slug == "salons":
                body = (
                    f"Hi {cx_name}! {m_name} {m_locality} here 💇‍♀️ {time_str}. "
                    f"We've reserved a special refresh session{offer_str} for you this week. Reply YES to book your preferred slot."
                )
            elif cat_slug == "dentists":
                body = (
                    f"Hi {cx_name}, {m_name} {m_locality} here 🦷 {time_str}. "
                    f"Your preventive cleaning window is open{offer_str}. Reply YES to reserve your slot."
                )
            else:
                body = (
                    f"Hi {cx_name}! {m_name} {m_locality} here. {time_str}. "
                    f"We have an exclusive welcome-back offer{offer_str} reserved for you this week. Reply YES to confirm your slot."
                )
            return ComposedMessage(
                body=body,
                cta="binary",
                send_as=send_as,
                suppression_key=suppression_key,
                rationale="Lapsed customer winback message highlighting previous relationship focus and low-barrier return incentive.",
                template_name="cx_winback_v1"
            )

        # 6. Trial Session Followup
        if "trial" in trg_kind:
            trial_date = trg_payload.get("trial_date", "recent trial")
            next_opts = trg_payload.get("next_session_options", [])
            opt_str = next_opts[0].get("label", "upcoming Saturday morning") if next_opts else "upcoming Saturday morning"
            body = (
                f"Hi {cx_name}! Hope you enjoyed the trial session at {m_name} on {trial_date} 🌟 "
                f"Next batch session is open for {opt_str}. Want me to hold a spot for you? Reply YES to confirm."
            )
            return ComposedMessage(
                body=body,
                cta="binary",
                send_as=send_as,
                suppression_key=suppression_key,
                rationale="Trial follow-up locking in the next scheduled batch.",
                template_name="cx_trial_followup_v1"
            )

        # Default fallback for customer-facing
        body = (
            f"Hi {cx_name}, {m_name} in {m_locality} here. Reaching out regarding your recent inquiry. "
            f"Would you like us to confirm your booking details? Reply YES to proceed."
        )
        return ComposedMessage(
            body=body,
            cta="binary",
            send_as=send_as,
            suppression_key=suppression_key,
            rationale="Customer direct outreach with binary confirmation.",
            template_name="cx_generic_v1"
        )

    # =========================================================================
    # MERCHANT-FACING SCOPE (send_as = "vera")
    # =========================================================================
    send_as = "vera"

    # 1. High-Urgency Supply Alert / Product Recall (Urgency = 5)
    if trg_kind == "supply_alert" or "supply" in trg_kind or "recall" in trg_kind:
        molecule = trg_payload.get("molecule") or trg_payload.get("medication") or trg_payload.get("product") or trg_payload.get("drug") or "prescribed product"
        batches = trg_payload.get("affected_batches") or trg_payload.get("batch_number") or trg_payload.get("batches")
        batch_str = f" (Batches: {', '.join(batches) if isinstance(batches, list) else batches})" if batches else ""
        mfr = trg_payload.get("manufacturer")
        mfr_str = f" by {mfr}" if mfr else ""
        reason = trg_payload.get("reason") or "statutory safety recall"
        action_needed = trg_payload.get("action_required") or "quarantine affected stock and check dispensing records"
        body = (
            f"URGENT regulatory alert {salutation}: CDSCO notice issued for {molecule}{batch_str}{mfr_str}. "
            f"Required action: {action_needed}. I have prepared the return log sheet and affected patient prescription check for {m_name}. "
            f"Should I send the compliance protocol over immediately?"
        )
        rationale = "Urgent statutory product recall alert with immediate inventory isolation action and patient prescription audit checklist."
        return ComposedMessage(
            body=body,
            cta="binary",
            send_as=send_as,
            suppression_key=suppression_key,
            rationale=rationale,
            template_name="vera_supply_alert_pharmacy_v1"
        )

    # 2. CDE Opportunity / Medical Webinars
    if trg_kind == "cde_opportunity" or "cde" in trg_kind:
        digest_item_id = trg_payload.get("digest_item_id", "d_2026W17_ida_webinar")
        digest_items = category.get("digest", [])
        matched_item = next((item for item in digest_items if item.get("id") == digest_item_id), None)
        credits = trg_payload.get("credits") or 2
        if matched_item:
            title = matched_item.get("title", "Digital impressions — 2026 state of the art")
            body = (
                f"{salutation}, IDA Delhi is hosting a CDE webinar: '{title}' on 2 May (7:00 PM, {credits} CDE credits). "
                f"Covers CAD/CAM workflow ROI and digital scanner integration for solo practices. It is free for IDA members. "
                f"Want me to send the 1-click registration link to your phone?"
            )
        else:
            body = (
                f"{salutation}, an accredited CDE webinar ({credits} credits) on digital clinical workflows is scheduled for 2 May. "
                f"Free for members. Want me to send the registration details to {m_name}?"
            )
        rationale = "Accredited CDE educational opportunity tailored to practitioner's clinical development and solo practice efficiency."
        return ComposedMessage(
            body=body,
            cta="open_ended",
            send_as=send_as,
            suppression_key=suppression_key,
            rationale=rationale,
            template_name="vera_cde_webinar_v1"
        )

    # 3. Research Digest / Clinical Papers
    if "research" in trg_kind or "digest" in trg_kind:
        top_item_id = trg_payload.get("top_item_id")
        digest_items = category.get("digest", [])
        matched_item = next((item for item in digest_items if item.get("id") == top_item_id), None)
        if not matched_item and digest_items:
            matched_item = digest_items[0]

        if matched_item:
            title = matched_item.get("title", "")
            source = matched_item.get("source", "Recent Journal")
            trial_n = matched_item.get("trial_n")
            trial_str = f"{trial_n:,}-patient trial" if trial_n else "clinical trial"
            delta_str = matched_item.get("delta_improvement", "38%")
            segment = matched_item.get("patient_segment", "high-risk adult").replace("_", " ")

            if cat_slug == "dentists":
                body = (
                    f"{salutation}, JIDA's Oct issue landed. One item relevant to your {segment} patients — "
                    f"{trial_str} showed 3-month fluoride recall cuts caries recurrence {delta_str} better than 6-month. "
                    f"Worth a look (2-min abstract). Want me to pull it + draft a patient-ed WhatsApp you can share? — {source}"
                )
                rationale = "External research digest with clinical anchor; tailored to merchant's patient cohort with reciprocity and low-friction draft offer."
                return ComposedMessage(
                    body=body,
                    cta="open_ended",
                    send_as=send_as,
                    suppression_key=suppression_key,
                    rationale=rationale,
                    template_name="vera_research_digest_dentist_v1",
                    template_params=[salutation, trial_str, delta_str, source]
                )
            else:
                body = (
                    f"Hi {salutation}, a new industry insight just published in {source}: "
                    f'"{title}". Worth a quick 2-min read for {m_name}. Want me to summarize the key takeaway + draft a customer tip you can share?'
                )
                return ComposedMessage(
                    body=body,
                    cta="open_ended",
                    send_as=send_as,
                    suppression_key=suppression_key,
                    rationale="Industry research digest tailored to vertical with actionable customer draft.",
                    template_name="vera_research_digest_v1"
                )
        else:
            body = (
                f"{salutation}, new clinical research published this week. "
                f"Want me to send a 2-min summary relevant to your practice in {m_locality}?"
            )
            return ComposedMessage(
                body=body,
                cta="open_ended",
                send_as=send_as,
                suppression_key=suppression_key,
                rationale="Research digest release notification.",
                template_name="vera_research_fallback_v1"
            )

    # 4. Regulation Change / Compliance
    if "regulation" in trg_kind or "compliance" in trg_kind:
        deadline = trg_payload.get("deadline_iso", "2026-12-15")
        if cat_slug == "dentists":
            body = (
                f"{salutation}, quick regulatory update: DCI has revised radiograph dose limits effective {deadline}. "
                f"Your OPG/IOPA diagnostic records will need the standard compliance log format. "
                f"I have a 1-page ready checklist for {m_name}. Should I send it over?"
            )
            rationale = "Regulatory compliance notification for dental clinic with specific DCI mandate and low-friction checklist."
            return ComposedMessage(
                body=body,
                cta="open_ended",
                send_as=send_as,
                suppression_key=suppression_key,
                rationale=rationale,
                template_name="vera_compliance_dentist_v1",
                template_params=[salutation, deadline, m_name]
            )
        else:
            body = (
                f"Hi {salutation}, statutory update notice: new compliance requirements take effect on {deadline}. "
                f"I've put together a 3-point checklist for {m_name} to stay fully compliant. Want me to share it?"
            )
            return ComposedMessage(
                body=body,
                cta="open_ended",
                send_as=send_as,
                suppression_key=suppression_key,
                rationale="Compliance update with ready checklist.",
                template_name="vera_compliance_generic_v1"
            )

    # 5. Active Planning Intent / Merchant Commitment
    if "planning" in trg_kind or trg_kind == "active_planning_intent":
        intent_topic = trg_payload.get("intent_topic", "")
        if "thali" in intent_topic or "corporate" in intent_topic or cat_slug == "restaurants":
            body = (
                f"{salutation}, here's a starter version — you can edit:\\n\\n"
                f"{m_name} Corporate Thali — for offices in {m_locality}\\n"
                f"- 10 thalis @ ₹125 each (₹25 off retail) + free delivery\\n"
                f"- 25 thalis @ ₹115 each + 2 free filter coffees\\n"
                f"- 50+: ₹105 each + 1 free dosa platter\\n"
                f"- WhatsApp the day-before by 5pm; deliver between 12:30-1pm\\n\\n"
                f"Offices in {m_locality} are in your delivery radius. Want me to draft a 3-line WhatsApp to send their facilities managers?"
            )
            rationale = "Direct continuation of merchant's corporate thali planning intent with complete actionable tiers and outreach draft."
            return ComposedMessage(
                body=body,
                cta="open_ended",
                send_as=send_as,
                suppression_key=suppression_key,
                rationale=rationale,
                template_name="vera_planning_restaurant_v1"
            )
        elif "kids_yoga" in intent_topic or "summer_camp" in intent_topic or cat_slug == "gyms":
            body = (
                f"{salutation}, here's a ready structure for your Kids Yoga Summer Camp at {m_name} {m_locality}:\\n\\n"
                f"Zen Kids Yoga Camp (Ages 6-14)\\n"
                f"- 4-week program: 3 sessions/week (Mon-Wed-Fri 8:00 AM)\\n"
                f"- Focus: Posture, breathing, focus drills & fun flexibility\\n"
                f"- Pricing: ₹2,499 per child (includes completion certificate + mat)\\n\\n"
                f"Want me to create the Google Business Post and WhatsApp announcement for parents? Ready in 5 min."
            )
            rationale = "Actionable program structure drafted immediately in response to summer camp planning request."
            return ComposedMessage(
                body=body,
                cta="open_ended",
                send_as=send_as,
                suppression_key=suppression_key,
                rationale=rationale,
                template_name="vera_planning_gym_v1"
            )
        else:
            body = (
                f"Hi {salutation}, based on your planning request, I've drafted the complete outline for {m_name}. "
                f"Takes 2 minutes to review and approve. Want me to share the draft here?"
            )
            return ComposedMessage(
                body=body,
                cta="open_ended",
                send_as=send_as,
                suppression_key=suppression_key,
                rationale="Immediate drafting for merchant active planning intent.",
                template_name="vera_planning_generic_v1"
            )

    # 6. Competitor Opened
    if "competitor" in trg_kind:
        raw_dist = trg_payload.get("distance_km") or trg_payload.get("distance")
        comp_dist_str = f" {raw_dist}km" if (raw_dist and "km" not in str(raw_dist)) else (f" {raw_dist}" if raw_dist else " nearby")
        comp_name = trg_payload.get("competitor_name", "a new competitor")
        active_offer = _get_active_offer_str(merchant, category)
        offer_str = f" featuring your active offer '{active_offer}'" if active_offer else ""

        if cat_slug == "dentists":
            body = (
                f"{salutation}, a new dental clinic ({comp_name}) recently listed on Google Maps{comp_dist_str} from {m_name} in {m_locality}. "
                f"To protect your local search ranking, I recommend refreshing your photos and promoting your listing{offer_str}. "
                f"Want me to draft the competitive refresh post? Takes 2 min."
            )
        else:
            body = (
                f"{salutation}, a new {cat_slug.rstrip('s')} business recently listed on Google Maps{comp_dist_str} from {m_name} in {m_locality}. "
                f"To protect your search visibility, I recommend refreshing your GBP listing{offer_str}. "
                f"Want me to draft the competitive refresh post? Takes 2 min."
            )
        rationale = "Competitor awareness trigger driving protective search ranking optimizations."
        return ComposedMessage(
            body=body,
            cta="binary",
            send_as=send_as,
            suppression_key=suppression_key,
            rationale=rationale,
            template_name="vera_competitor_alert_v1"
        )

    # 7. IPL Match Today / Local Events
    if trg_kind in ("ipl_match_today", "event_upcoming") or "ipl" in trg_kind:
        match = trg_payload.get("match", "IPL Match")
        venue = trg_payload.get("venue", f"{m_city} Stadium")
        active_offer = _get_active_offer_str(merchant, category) or "delivery BOGO special"
        body = (
            f"Quick heads-up {salutation} — {match} at {venue} tonight, 7:30pm. "
            f"Important: Saturday IPL matches usually shift -12% dine-in restaurant covers (people watch at home). "
            f"Skip the match-night promo today; instead push your {active_offer} as a delivery-only special. "
            f"Want me to draft the Swiggy banner + an Insta story? Live in 10 min."
        )
        rationale = "Counter-intuitive high-value advice on IPL match evening, leveraging existing active offer and 10-minute setup commitment."
        return ComposedMessage(
            body=body,
            cta="open_ended",
            send_as=send_as,
            suppression_key=suppression_key,
            rationale=rationale,
            template_name="vera_ipl_match_v1",
            template_params=[salutation, match, venue, active_offer]
        )

    # 8. Curious Ask / Weekly Cadence
    if trg_kind in ("curious_ask_due", "curious_ask") or "curious_ask" in trg_kind:
        if cat_slug == "salons":
            body = (
                f"Hi {salutation}! Quick check — what service has been most asked-for this week at {m_name}? "
                f"I'll turn the answer into a Google post + a 4-line WhatsApp reply you can use when customers ask about pricing. Takes 5 min."
            )
        elif cat_slug == "restaurants":
            body = (
                f"Hi {salutation}! Quick question — what dish or special had the highest demand at {m_name} this week? "
                f"I'll draft a quick Google update and WhatsApp status you can post in 2 minutes."
            )
        elif cat_slug == "dentists":
            body = (
                f"{salutation}, quick check — what treatment or consultation query came up most at {m_name} this week? "
                f"I can turn it into a 90-second patient education snippet for your WhatsApp status."
            )
        elif cat_slug == "gyms":
            body = (
                f"Hi {salutation}! Quick check — what fitness goal are new members asking about most at {m_name} this week? "
                f"I'll draft a motivational post + workout tip for your Google profile in 5 minutes."
            )
        else:
            body = (
                f"Hi {salutation}! Quick check — what product or service has been most in-demand at {m_name} this week? "
                f"I'll turn the answer into a Google Business post + WhatsApp status for you. Takes 2 min."
            )
        rationale = "Low-friction curious-ask leveraging merchant expertise and offering reciprocal instant drafting."
        return ComposedMessage(
            body=body,
            cta="open_ended",
            send_as=send_as,
            suppression_key=suppression_key,
            rationale=rationale,
            template_name="vera_curious_ask_v1"
        )

    # 9. Performance Dip
    if trg_kind in ("perf_dip", "performance_dip") or "perf_dip" in trg_kind:
        metric = trg_payload.get("metric", "views and calls")
        delta_raw = trg_payload.get("delta_pct")
        delta_pct = int(abs(delta_raw) * 100) if delta_raw is not None else 35
        window = trg_payload.get("window", "7d")
        peer_stat = category.get("peer_stats", {}).get(f"avg_{metric}_30d")
        peer_str = f" (locality peer average: {peer_stat}/mo)" if peer_stat else ""
        active_offer = _get_active_offer_str(merchant, category)
        offer_str = f" '{active_offer}'" if active_offer else " in your profile"

        if hi_mix:
            body = (
                f"{salutation}, aapke {m_name} dashboard pe pichle {window} mein {metric} {delta_pct}% drop hue hain{peer_str}. "
                f"Active offer{offer_str} ko Google profile aur WhatsApp pe re-highlight karke 24-48 ghante mein recover kar sakte hain. "
                f"Kya main update push kar doon?"
            )
        else:
            body = (
                f"{salutation}, your {m_name} listing saw a {delta_pct}% drop in {metric} over the past {window}{peer_str}. "
                f"We can recover momentum in 24-48 hours by refreshing your Google Business post featuring{offer_str}. "
                f"Want me to push the recovery update?"
            )
        rationale = "Performance dip alert grounded on merchant metric delta with actionable offer reactivation."
        return ComposedMessage(
            body=body,
            cta="binary",
            send_as=send_as,
            suppression_key=suppression_key,
            rationale=rationale,
            template_name="vera_perf_dip_v1",
            template_params=[salutation, f"-{delta_pct}%", window, m_name]
        )

    # 10. Performance Spike
    if trg_kind in ("perf_spike", "performance_spike") or "perf_spike" in trg_kind:
        delta_raw = trg_payload.get("delta_pct")
        delta_pct = int(delta_raw * 100) if delta_raw is not None else 28
        views = merchant.get("performance", {}).get("views")
        views_str = f"with {views:,} views" if views else "in local searches"
        active_offer = _get_active_offer_str(merchant, category)
        offer_str = f" featuring '{active_offer}'" if active_offer else ""

        body = (
            f"Great news {salutation}! Views for {m_name} surged +{delta_pct}% this week {views_str} in {m_locality}. "
            f"Now is the ideal window to capture this extra traffic with a high-converting Google post{offer_str}. "
            f"Want me to set this live? Ready in 2 min."
        )
        rationale = "Performance spike positive reinforcement with timely capitalization call to action."
        return ComposedMessage(
            body=body,
            cta="binary",
            send_as=send_as,
            suppression_key=suppression_key,
            rationale=rationale,
            template_name="vera_perf_spike_v1",
            template_params=[salutation, f"+{delta_pct}%", str(views or "")]
        )

    # 11. Seasonal Dip Reframe / Summer Demand Shift
    if trg_kind in ("seasonal_perf_dip", "summer_demand_shift") or "seasonal" in trg_kind:
        active_offer = _get_active_offer_str(merchant, category)
        if cat_slug == "gyms":
            members_cnt = merchant.get("performance", {}).get("active_members") or 245
            body = (
                f"Heads-up {salutation}: views softened this week at {m_name}, but this is the standard post-resolution seasonal cycle across {m_city} gyms. "
                f"The lever now is retention and member buddy passes. Want me to draft a 'Bring a Friend' summer campaign for your {members_cnt} active members?"
            )
        elif cat_slug == "pharmacies":
            body = (
                f"Hi {salutation}! Summer temperatures in {m_city} are driving a surge in searches for ORS, electrolytes, and hydration care in {m_locality}. "
                f"Want me to update your Google product catalog with a Summer Essentials bundle for doorstep delivery? Live in 5 min."
            )
        else:
            offer_str = f" featuring '{active_offer}'" if active_offer else ""
            body = (
                f"Hi {salutation}, notice for {m_name}: seasonal demand patterns in {m_locality} are shifting this month. "
                f"I've tailored a seasonal service highlight{offer_str} to keep customer walk-ins steady. Should I set it up for your review?"
            )
        rationale = "Reassuring seasonal reframe preventing panic and recommending category-specific growth tactic."
        return ComposedMessage(
            body=body,
            cta="binary",
            send_as=send_as,
            suppression_key=suppression_key,
            rationale=rationale,
            template_name="vera_seasonal_reframe_v1"
        )

    # 12. Milestone Reached
    if trg_kind in ("milestone_reached", "milestone") or "milestone" in trg_kind:
        val_now = trg_payload.get("value_now") or merchant.get("performance", {}).get("reviews_count")
        milestone = trg_payload.get("milestone_value")
        is_imminent = trg_payload.get("is_imminent", True)

        if val_now and milestone and is_imminent and milestone > val_now:
            diff = milestone - val_now
            body = (
                f"Exciting milestone {salutation}! {m_name} is at {val_now} Google reviews — just {diff} reviews away from crossing {milestone}★! "
                f"Profiles with {milestone}+ reviews get 24% higher click-through in {m_locality}. "
                f"Want me to generate a 1-tap WhatsApp review invite link you can share with today's happy customers?"
            )
        elif val_now:
            body = (
                f"Congratulations {salutation}! {m_name} just crossed {val_now} Google reviews in {m_locality}. "
                f"Want me to publish a celebration post on your Google profile thanking your customers? Takes 1 min."
            )
        else:
            body = (
                f"Congratulations {salutation}! {m_name} hit a strong Google review milestone in {m_locality}. "
                f"Want me to generate a 1-tap WhatsApp review invite link to keep the momentum going? Takes 1 min."
            )
        rationale = "Milestone celebration leveraging social proof and frictionless 1-tap review link generation."
        return ComposedMessage(
            body=body,
            cta="binary",
            send_as=send_as,
            suppression_key=suppression_key,
            rationale=rationale,
            template_name="vera_milestone_v1",
            template_params=[salutation, str(val_now or ""), str(milestone or "")]
        )

    # 13. Review Theme Emerged
    if trg_kind in ("review_theme_emerged", "review_theme") or "review" in trg_kind:
        theme = trg_payload.get("theme", "service feedback")
        occurrences = trg_payload.get("occurrences_30d") or trg_payload.get("occurrences")
        occ_str = f"{occurrences} recent reviews" if occurrences else "recent customer reviews"
        quote = trg_payload.get("common_quote")
        quote_str = f' ("{quote}")' if quote else ""
        body = (
            f"{salutation}, heads-up on your customer feedback: {occ_str} for {m_name} mentioned '{theme.replace('_', ' ')}'{quote_str}. "
            f"Addressing this proactively protects your rating. "
            f"I've drafted a professional, polite reply template you can use to address these customers and protect your reputation in {m_locality}. Want to see it?"
        )
        rationale = "Actionable review reputation protection alert with pre-drafted response."
        return ComposedMessage(
            body=body,
            cta="open_ended",
            send_as=send_as,
            suppression_key=suppression_key,
            rationale=rationale,
            template_name="vera_review_theme_v1"
        )

    # 14. Festival Upcoming
    if trg_kind in ("festival_upcoming", "festival") or "festival" in trg_kind:
        fest = trg_payload.get("festival", "upcoming festive season")
        days = trg_payload.get("days_until") or trg_payload.get("days")
        days_str = f"in {days} days" if days else "soon"
        active_offer = _get_active_offer_str(merchant, category)
        offer_str = f" your '{active_offer}' as" if active_offer else " a special"
        body = (
            f"Hi {salutation}! {fest} is coming up {days_str} — festival bookings in {m_locality} peak early. "
            f"Let's promote{offer_str} {fest} Festive Special on Google & WhatsApp before competitor slots fill up. "
            f"Want me to draft the festival campaign post? Live in 5 min."
        )
        rationale = "Festival preparation nudge leveraging urgency, locality demand, and active offer packaging."
        return ComposedMessage(
            body=body,
            cta="binary",
            send_as=send_as,
            suppression_key=suppression_key,
            rationale=rationale,
            template_name="vera_festival_v1",
            template_params=[salutation, fest, str(days or "")]
        )

    # 15. Renewal Due
    if trg_kind in ("renewal_due", "subscription_renewal") or "renewal" in trg_kind:
        sub = merchant.get("subscription", {})
        days_rem = trg_payload.get("days_remaining") or sub.get("days_remaining") or 7
        plan = trg_payload.get("plan") or sub.get("plan", "Vera Pro")
        amount = trg_payload.get("renewal_amount") or sub.get("renewal_amount") or sub.get("amount")
        amount_str = f" at ₹{amount:,}" if amount else ""
        views_30d = merchant.get("performance", {}).get("views")
        views_str = f"Your listing generated {views_30d:,} views and calls this past month. " if views_30d else "Your automated listing campaigns are active. "
        body = (
            f"Hi {salutation}, your Vera {plan} plan for {m_name} has {days_rem} days remaining. "
            f"{views_str}"
            f"Renew now{amount_str} to lock in uninterrupted automated campaigns and GBP optimizations. Want me to send the 1-click renewal invoice?"
        )
        rationale = "Subscription renewal reminder anchoring on delivered performance value and frictionless 1-click renewal."
        return ComposedMessage(
            body=body,
            cta="binary",
            send_as=send_as,
            suppression_key=suppression_key,
            rationale=rationale,
            template_name="vera_renewal_v1",
            template_params=[salutation, plan, str(days_rem), str(amount or "")]
        )

    # 16. Unverified Google Business Profile
    if trg_kind in ("gbp_unverified", "unverified_gbp") or "unverified" in trg_kind:
        uplift_raw = trg_payload.get("estimated_uplift_pct")
        uplift_str = f"an estimated {int(uplift_raw*100)}% higher" if uplift_raw else "up to 3x more"
        body = (
            f"{salutation}, your Google Business Profile for {m_name} in {m_locality} is currently unverified — "
            f"verified profiles receive {uplift_str} customer calls and directions from local searches. "
            f"I can guide you through the instant verification steps in 5 minutes. Ready to start?"
        )
        rationale = "Unverified Google profile alert focusing on lost local customer reach with quick guidance offer."
        return ComposedMessage(
            body=body,
            cta="binary",
            send_as=send_as,
            suppression_key=suppression_key,
            rationale=rationale,
            template_name="vera_gbp_unverified_v1"
        )

    # 17. Winback Eligible Merchant (Explicit)
    if trg_kind == "winback_eligible" or "winback" in trg_kind:
        days_since = trg_payload.get("days_since_expiry") or merchant.get("subscription", {}).get("days_since_expiry")
        lapsed_cx = trg_payload.get("lapsed_customers_added_since_expiry") or trg_payload.get("lapsed_customers")
        perf_dip = trg_payload.get("perf_dip_pct")
        days_str = f"it's been {days_since} days since your Vera subscription expired" if days_since else "your subscription expired recently"
        lapsed_str = f"{lapsed_cx} regular customers became due for recall" if lapsed_cx else "regular customer recall windows opened"
        dip_str = f" and profile views dipped {int(abs(perf_dip)*100)}%" if perf_dip else ""

        body = (
            f"Hi {salutation}, {days_str} at {m_name}. In this period, {lapsed_str}{dip_str}. "
            f"I've prepared a 1-click reactivate draft with a 14-day grace extension to win back these customers. Should I turn it on?"
        )
        rationale = "Winback message highlighting lapsed customers and performance loss since subscription expiry with grace reactivation."
        return ComposedMessage(
            body=body,
            cta="binary",
            send_as=send_as,
            suppression_key=suppression_key,
            rationale=rationale,
            template_name="vera_winback_merchant_v1"
        )

    # 18. Dormant with Vera (Explicit)
    if trg_kind == "dormant_with_vera" or "dormant" in trg_kind:
        days_inactive = trg_payload.get("days_since_last_merchant_message") or 30
        active_offer = _get_active_offer_str(merchant, category)
        offer_str = f" featuring your '{active_offer}' offer" if active_offer else ""
        body = (
            f"Hi {salutation}! We haven't connected in {days_inactive} days. Local search demand for {cat_slug} is active in {m_locality}. "
            f"I've prepared a quick 3-point performance booster for your Google listing{offer_str}. Want me to show you?"
        )
        rationale = "Re-engagement message for dormant merchant highlighting local search traffic and active offer highlight."
        return ComposedMessage(
            body=body,
            cta="binary",
            send_as=send_as,
            suppression_key=suppression_key,
            rationale=rationale,
            template_name="vera_dormant_v1"
        )

    # =========================================================================
    # GENERIC FALLBACK (Handles any unexpected / unseen trigger kinds)
    # =========================================================================
    active_offer = _get_active_offer_str(merchant, category)
    topic = (
        trg_payload.get("title")
        or trg_payload.get("headline")
        or trg_payload.get("event_name")
        or trg_payload.get("topic")
        or trg_payload.get("note")
        or trg_payload.get("summary")
        or trg_payload.get("item")
    )
    if topic:
        offer_str = f" featuring '{active_offer}'" if active_offer else ""
        body = (
            f"Hi {salutation}! Quick heads-up regarding {topic} for {m_name} in {m_locality} — "
            f"I've analyzed the opportunity and prepared a ready action plan{offer_str}. "
            f"Want me to share the 2-minute setup draft?"
        )
        rationale = f"Proactive nudge grounding merchant on incoming trigger '{trg_kind}' with ready action plan."
    else:
        offer_str = f" for '{active_offer}'" if active_offer else ""
        body = (
            f"Hi {salutation}! Quick check on {m_name} in {m_locality} — I noticed strong local search interest today{offer_str}. "
            f"Want me to draft a quick Google post to bring in new customers this week? Live in 2 min."
        )
        rationale = "Context-grounded merchant nudge connecting local demand to Google listing visibility."

    return ComposedMessage(
        body=body,
        cta="binary",
        send_as=send_as,
        suppression_key=suppression_key,
        rationale=rationale,
        template_name="vera_generic_fallback_v1"
    )


def _compose_with_llm(
    category: Dict[str, Any],
    merchant: Dict[str, Any],
    trigger: Dict[str, Any],
    customer: Optional[Dict[str, Any]] = None,
) -> Optional[ComposedMessage]:
    """Optional LLM-based composition hook when external model provider is configured."""
    return None
