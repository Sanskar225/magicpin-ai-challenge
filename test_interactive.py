"""
Interactive Manual Testing CLI for Vera Message Engine.
Run: python test_interactive.py
Allows you to test all 5 verticals, any trigger, and multi-turn conversations live.
"""

from __future__ import annotations
import sys
import io
import json
import glob
from pathlib import Path

# Ensure UTF-8 output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from engine.composer import compose
from engine.reply_handler import ConversationManager


def load_dataset():
    categories = {}
    for f in glob.glob("expanded/categories/*.json"):
        with open(f, "r", encoding="utf-8") as fp:
            d = json.load(fp)
            categories[d.get("slug")] = d

    merchants = {}
    for f in glob.glob("expanded/merchants/*.json"):
        with open(f, "r", encoding="utf-8") as fp:
            d = json.load(fp)
            merchants[d.get("merchant_id")] = d

    triggers = {}
    for f in glob.glob("expanded/triggers/*.json"):
        with open(f, "r", encoding="utf-8") as fp:
            d = json.load(fp)
            triggers[d.get("id")] = d

    return categories, merchants, triggers


def main():
    print("=" * 65)
    print("      VERA MESSAGE ENGINE — INTERACTIVE MANUAL TESTER        ")
    print("=" * 65)

    categories, merchants, triggers = load_dataset()
    conv_mgr = ConversationManager()

    while True:
        print("\n--- SELECT AN ACTION ---")
        print("1. Test Composer (Pick Merchant + Trigger)")
        print("2. Test Multi-Turn Reply (Simulate Chat with Vera)")
        print("3. Quick Test 5 Anchor Case Studies (Dentist, Salon, Restaurant, Gym, Pharmacy)")
        print("4. Test Custom Judge Scenarios (Supply Alert, Unverified GBP, Winback, Pricing)")
        print("5. Exit")

        choice = input("\nEnter choice (1-5): ").strip()

        if choice == "1":
            print("\nAvailable Categories:", list(categories.keys()))
            m_list = list(merchants.keys())
            print("\nSample Merchants:")
            for i, m_id in enumerate(m_list[:10]):
                m = merchants[m_id]
                print(f"  {i+1}. {m_id} ({m.get('identity',{}).get('name')}) - [{m.get('category_slug')}]")

            sel_m = input("\nEnter Merchant number or ID: ").strip()
            if sel_m.isdigit() and 1 <= int(sel_m) <= len(m_list):
                merchant_id = m_list[int(sel_m) - 1]
            else:
                merchant_id = sel_m if sel_m in merchants else m_list[0]

            merchant = merchants[merchant_id]
            cat_slug = merchant.get("category_slug", "dentists")
            category = categories.get(cat_slug, {"slug": cat_slug})

            t_list = list(triggers.keys())
            print("\nSample Triggers:")
            for i, t_id in enumerate(t_list[:12]):
                t = triggers[t_id]
                print(f"  {i+1}. {t_id} (kind: {t.get('kind')}) [urgency: {t.get('urgency', 1)}]")

            sel_t = input("\nEnter Trigger number or ID: ").strip()
            if sel_t.isdigit() and 1 <= int(sel_t) <= len(t_list):
                trigger_id = t_list[int(sel_t) - 1]
            else:
                trigger_id = sel_t if sel_t in triggers else t_list[0]

            trigger = triggers[trigger_id]

            # Compose message
            result = compose(category, merchant, trigger)

            print("\n" + "=" * 50)
            print(" COMPOSED VERA OUTPUT:")
            print("=" * 50)
            print(f"Send As:         {result.send_as}")
            print(f"CTA Type:        {result.cta}")
            print(f"Template Name:   {result.template_name}")
            print(f"Suppression Key: {result.suppression_key}")
            print(f"\nMessage Body:\n\"{result.body}\"")
            print(f"\nRationale:\n{result.rationale}")
            print("=" * 50)

        elif choice == "2":
            merchant = merchants.get("m_001_drmeera_dentist_delhi")
            category = categories.get("dentists")
            conv_id = "test_manual_conv_1"

            print(f"\nSimulating conversation with: {merchant['identity']['name']}")
            print("Type any reply as the merchant (e.g., 'Ok lets do it', 'What is your price?', 'Stop', 'Help with GST', 'Busy call later').")
            print("Type 'exit' to return to menu.\n")

            while True:
                user_msg = input("You (Merchant) > ").strip()
                if user_msg.lower() == "exit":
                    break
                if not user_msg:
                    continue

                reply_res = conv_mgr.handle_reply(
                    conversation_id=conv_id,
                    merchant_id="m_001_drmeera_dentist_delhi",
                    customer_id=None,
                    from_role="merchant",
                    message=user_msg,
                    turn_number=2,
                    merchant_context=merchant,
                    category_context=category,
                )

                print(f"\nVera Action: [{reply_res.action.upper()}]")
                if reply_res.body:
                    print(f"Vera Body: \"{reply_res.body}\"")
                if reply_res.wait_seconds:
                    print(f"Wait Seconds: {reply_res.wait_seconds}s")
                print(f"Rationale: {reply_res.rationale}\n")

                if reply_res.action == "end":
                    print("--- [Conversation Terminated by Bot] ---\n")
                    break

        elif choice == "3":
            anchors = [
                ("Dentists", "m_001_drmeera_dentist_delhi", "trg_001_cde_webinar_meera"),
                ("Salons", "m_004_glamour_salon_pune", "trg_004_bridal_package_glamour"),
                ("Restaurants", "m_005_pizzajunction_restaurant_delhi", "trg_005_ipl_match_pizza"),
                ("Gyms", "m_007_powerhouse_gym_bangalore", "trg_007_summer_camp_planning_powerhouse"),
                ("Pharmacies", "m_009_apollo_pharmacy_jaipur", "trg_018_supply_atorvastatin_recall"),
            ]
            for cat_name, mid, tid in anchors:
                m = merchants[mid]
                c = categories[m["category_slug"]]
                t = triggers[tid]
                res = compose(c, m, t)
                print(f"\n--- {cat_name.upper()}: {m['identity']['name']} ---")
                print(f"Trigger: {tid} ({t.get('kind')})")
                print(f"Body: \"{res.body}\"")
                print(f"CTA: {res.cta} | Suppression: {res.suppression_key}")

        elif choice == "4":
            print("\n--- 1. Supply Alert Recall (Pharmacy, Urgency 5) ---")
            m_pharm = merchants["m_009_apollo_pharmacy_jaipur"]
            t_supply = triggers["trg_018_supply_atorvastatin_recall"]
            print("Output:", compose(categories["pharmacies"], m_pharm, t_supply).body)

            print("\n--- 2. Unverified GBP (Pharmacy) ---")
            m_sun = merchants["m_010_sunrisepharm_pharmacy_lucknow"]
            t_gbp = triggers["trg_021_unverified_gbp_sunrise"]
            print("Output:", compose(categories["pharmacies"], m_sun, t_gbp).body)

            print("\n--- 3. Winback Lapsed Merchant ---")
            m_salon = merchants["m_004_glamour_salon_pune"]
            t_wb = triggers["trg_009_winback_glamour"]
            print("Output:", compose(categories["salons"], m_salon, t_wb).body)

            print("\n--- 4. Dormancy Re-engagement ---")
            t_dm = triggers["trg_025_dormancy_glamour"]
            print("Output:", compose(categories["salons"], m_salon, t_dm).body)

        elif choice == "5":
            print("Exiting manual tester. All systems ready!")
            break


if __name__ == "__main__":
    main()