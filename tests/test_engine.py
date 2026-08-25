"""
Automated Unit Tests for the Vera Message Engine & API Server.
"""

import json
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

from bot import app, context_store
from engine.composer import compose
from engine.reply_handler import ConversationManager


class TestVeraEngine(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        context_store.clear()

    def test_healthz(self):
        resp = self.client.get("/v1/healthz")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("contexts_loaded", data)
        self.assertEqual(data["contexts_loaded"]["category"], 0)

    def test_metadata(self):
        resp = self.client.get("/v1/metadata")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("team_name", data)
        self.assertIn("model", data)
        self.assertIn("version", data)

    def test_context_push_idempotency_and_versioning(self):
        # 1. Push version 1 -> Expect 200 (Accepted)
        payload_v1 = {"slug": "dentists", "display_name": "Dentists"}
        r1 = self.client.post("/v1/context", json={
            "scope": "category",
            "context_id": "dentists",
            "version": 1,
            "payload": payload_v1,
            "delivered_at": "2026-04-26T10:00:00Z"
        })
        self.assertEqual(r1.status_code, 200)
        self.assertTrue(r1.json()["accepted"])

        # 2. Re-push version 1 -> Expect 200 (Idempotent No-Op)
        r2 = self.client.post("/v1/context", json={
            "scope": "category",
            "context_id": "dentists",
            "version": 1,
            "payload": payload_v1,
            "delivered_at": "2026-04-26T10:00:00Z"
        })
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json()["accepted"])

        # 3. Push version 2 -> Expect 200 (Replaces atomically)
        payload_v2 = {"slug": "dentists", "display_name": "Dentists Updated"}
        r3 = self.client.post("/v1/context", json={
            "scope": "category",
            "context_id": "dentists",
            "version": 2,
            "payload": payload_v2,
            "delivered_at": "2026-04-26T10:15:00Z"
        })
        self.assertEqual(r3.status_code, 200)
        self.assertTrue(r3.json()["accepted"])
        self.assertEqual(context_store[("category", "dentists")]["payload"]["display_name"], "Dentists Updated")

        # 4. Push stale version 1 when version 2 exists -> Expect 409 Conflict
        r4 = self.client.post("/v1/context", json={
            "scope": "category",
            "context_id": "dentists",
            "version": 1,
            "payload": payload_v1,
            "delivered_at": "2026-04-26T10:20:00Z"
        })
        self.assertEqual(r4.status_code, 409)
        self.assertFalse(r4.json()["accepted"])
        self.assertEqual(r4.json()["reason"], "stale_version")
        self.assertEqual(r4.json()["current_version"], 2)

    def test_tick_and_action_generation(self):
        # Push Category
        self.client.post("/v1/context", json={
            "scope": "category",
            "context_id": "dentists",
            "version": 1,
            "payload": {
                "slug": "dentists",
                "voice": {"tone": "peer_clinical"},
                "offer_catalog": [{"title": "Dental Cleaning @ ₹299", "type": "service_at_price"}],
                "peer_stats": {"avg_ctr": 0.030},
                "digest": [{
                    "id": "d_2026W17_jida_fluoride",
                    "title": "3-mo fluoride recall cuts caries 38% better",
                    "source": "JIDA Oct 2026, p.14",
                    "trial_n": 2100,
                    "patient_segment": "high_risk_adults"
                }]
            }
        })

        # Push Merchant
        self.client.post("/v1/context", json={
            "scope": "merchant",
            "context_id": "m_001_drmeera",
            "version": 1,
            "payload": {
                "merchant_id": "m_001_drmeera",
                "category_slug": "dentists",
                "identity": {"name": "Dr. Meera's Dental Clinic", "locality": "Lajpat Nagar", "owner_first_name": "Meera"},
                "offers": [{"id": "o1", "title": "Dental Cleaning @ ₹299", "status": "active"}]
            }
        })

        # Push Trigger
        self.client.post("/v1/context", json={
            "scope": "trigger",
            "context_id": "trg_test_01",
            "version": 1,
            "payload": {
                "id": "trg_test_01",
                "scope": "merchant",
                "kind": "research_digest",
                "merchant_id": "m_001_drmeera",
                "payload": {"top_item_id": "d_2026W17_jida_fluoride"},
                "suppression_key": "research:dentists:2026-W17"
            }
        })

        # Call Tick
        resp = self.client.post("/v1/tick", json={
            "now": "2026-04-26T10:30:00Z",
            "available_triggers": ["trg_test_01"]
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["actions"]), 1)
        action = data["actions"][0]
        self.assertEqual(action["merchant_id"], "m_001_drmeera")
        self.assertIn("JIDA Oct 2026, p.14", action["body"])
        self.assertIn("2,100", action["body"])
        self.assertIn("Dr. Meera", action["body"])
        self.assertEqual(action["send_as"], "vera")

    def test_reply_auto_reply_detection(self):
        # Simulate merchant canned auto-reply
        auto_msg = "Thank you for contacting us! Our team will respond shortly."
        r1 = self.client.post("/v1/reply", json={
            "conversation_id": "conv_auto_test",
            "merchant_id": "m_001",
            "from_role": "merchant",
            "message": auto_msg,
            "received_at": "2026-04-26T10:00:00Z",
            "turn_number": 1
        })
        self.assertEqual(r1.status_code, 200)
        # Should detect auto-reply pattern
        self.assertEqual(r1.json()["action"], "end")
        self.assertIn("auto-reply", r1.json()["rationale"].lower())

    def test_reply_intent_transition_to_action(self):
        # Merchant says "Ok lets do it. Whats next?"
        r = self.client.post("/v1/reply", json={
            "conversation_id": "conv_intent_test",
            "merchant_id": "m_001",
            "from_role": "merchant",
            "message": "Ok lets do it. Whats next?",
            "received_at": "2026-04-26T10:00:00Z",
            "turn_number": 2
        })
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["action"], "send")
        body_lower = data["body"].lower()

        # Must switch to action mode, not re-qualify
        qualifying = ["would you", "do you", "can you tell", "what if", "how about"]
        actioning = ["done", "sending", "draft", "here", "confirm", "proceed", "next"]

        self.assertTrue(any(w in body_lower for w in actioning))
        self.assertFalse(any(w in body_lower for w in qualifying))

    def test_reply_hostility_handling(self):
        # Merchant expresses hostility
        r = self.client.post("/v1/reply", json={
            "conversation_id": "conv_hostile_test",
            "merchant_id": "m_001",
            "from_role": "merchant",
            "message": "Stop messaging me. This is useless spam.",
            "received_at": "2026-04-26T10:00:00Z",
            "turn_number": 2
        })
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["action"], "end")

    def test_submission_file_integrity(self):
        sub_file = Path(__file__).parent.parent / "submission.jsonl"
        self.assertTrue(sub_file.exists())
        with open(sub_file, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        self.assertEqual(len(lines), 30)

        for i, line in enumerate(lines, 1):
            entry = json.loads(line)
            self.assertIn("test_id", entry)
            self.assertIn("body", entry)
            self.assertIn("cta", entry)
            self.assertIn("send_as", entry)
            self.assertIn("suppression_key", entry)
            self.assertIn("rationale", entry)
            self.assertTrue(len(entry["body"]) > 20)


if __name__ == "__main__":
    unittest.main()
