"""
Generate submission.jsonl from expanded dataset test pairs.
Produces 30 lines matching the challenge submission specification.
"""

from __future__ import annotations
import json
from pathlib import Path
from engine.composer import compose


def main():
    root_dir = Path(__file__).parent
    expanded_dir = root_dir / "expanded"

    if not expanded_dir.exists():
        print("expanded directory not found, using dataset seeds...")
        expanded_dir = root_dir / "dataset"

    test_pairs_path = expanded_dir / "test_pairs.json"
    if not test_pairs_path.exists():
        test_pairs_path = root_dir / "dataset" / "test_pairs.json"

    with open(test_pairs_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    pairs = data.get("pairs", [])
    print(f"Loaded {len(pairs)} test pairs.")

    # Load categories cache
    categories = {}
    cat_dir = expanded_dir / "categories"
    if not cat_dir.exists():
        cat_dir = root_dir / "dataset" / "categories"
    for cat_file in cat_dir.glob("*.json"):
        with open(cat_file, "r", encoding="utf-8") as f:
            cdata = json.load(f)
            categories[cdata.get("slug", cat_file.stem)] = cdata

    out_file = root_dir / "submission.jsonl"
    lines = []

    for item in pairs:
        test_id = item["test_id"]
        trg_id = item["trigger_id"]
        merchant_id = item["merchant_id"]
        customer_id = item.get("customer_id")

        # Load trigger
        trg_file = expanded_dir / "triggers" / f"{trg_id}.json"
        if trg_file.exists():
            with open(trg_file, "r", encoding="utf-8") as f:
                trigger = json.load(f)
        else:
            # Fallback to seeds
            with open(root_dir / "dataset" / "triggers_seed.json", "r", encoding="utf-8") as f:
                tseeds = json.load(f).get("triggers", [])
                trigger = next((t for t in tseeds if t["id"] == trg_id), {"id": trg_id, "kind": "generic", "payload": {}})

        # Load merchant
        m_file = expanded_dir / "merchants" / f"{merchant_id}.json"
        if m_file.exists():
            with open(m_file, "r", encoding="utf-8") as f:
                merchant = json.load(f)
        else:
            with open(root_dir / "dataset" / "merchants_seed.json", "r", encoding="utf-8") as f:
                mseeds = json.load(f).get("merchants", [])
                merchant = next((m for m in mseeds if m["merchant_id"] == merchant_id), {"merchant_id": merchant_id, "identity": {"name": merchant_id}})

        cat_slug = merchant.get("category_slug", "dentists")
        category = categories.get(cat_slug, {"slug": cat_slug})

        # Load customer if present
        customer = None
        if customer_id:
            c_file = expanded_dir / "customers" / f"{customer_id}.json"
            if c_file.exists():
                with open(c_file, "r", encoding="utf-8") as f:
                    customer = json.load(f)
            else:
                with open(root_dir / "dataset" / "customers_seed.json", "r", encoding="utf-8") as f:
                    cseeds = json.load(f).get("customers", [])
                    customer = next((c for c in cseeds if c["customer_id"] == customer_id), None)

        composed = compose(
            category=category,
            merchant=merchant,
            trigger=trigger,
            customer=customer,
        )

        sub_entry = {
            "test_id": test_id,
            "body": composed.body,
            "cta": composed.cta,
            "send_as": composed.send_as,
            "suppression_key": composed.suppression_key,
            "rationale": composed.rationale,
        }
        lines.append(json.dumps(sub_entry, ensure_ascii=False))

    with open(out_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"Successfully generated {len(lines)} submission lines to {out_file}")


if __name__ == "__main__":
    main()
