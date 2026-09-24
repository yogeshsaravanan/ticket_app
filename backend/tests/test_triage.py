"""Core logic: classification, urgency, actions, queue ordering."""
import unittest

from app.queue_store import TicketStore
from app.triage import classify, handle_ticket, score_urgency


class Classify(unittest.TestCase):
    def check(self, text, decision, category=None):
        r = classify(text)
        self.assertEqual(r["decision"], decision, (text, r))
        self.assertEqual(r["category"], category, (text, r))
        return r

    def test_clear_matches(self):
        self.check("I forgot my password and can't log in", "action", "password_reset")
        self.check("Please reset my password", "action", "password_reset")
        self.check("Can you check my billing status? I think my invoice is wrong",
                   "action", "billing_status")
        self.check("I was charged twice this month", "action", "billing_status")
        self.check("Our API server is not responding, please restart it",
                   "action", "service_restart")
        self.check("The service keeps going down with 502 errors", "action", "service_restart")

    def test_escalates_instead_of_guessing(self):
        r = self.check("How do I change the email on my account?", "escalate")
        self.assertIn("Not guessing", r["reason"])
        self.check("hello", "escalate")
        self.check("I love your product!", "escalate")
        # a single weak keyword is not enough to act
        self.check("what are the password requirements", "escalate")

    def test_ambiguous_escalates(self):
        r = self.check("The app crashed right after I updated my payment method", "escalate")
        self.assertIn("Ambiguous", r["reason"])

    def test_security_overrides_category(self):
        # would otherwise be a clean password_reset: must not auto-reset
        r = self.check("My account was hacked, please reset my password", "escalate")
        self.assertIn("security", r["reason"].lower())
        self.check("My account was compromised", "escalate")
        self.check("I see an unauthorized charge on my invoice", "escalate")

    def test_legal_escalates(self):
        self.check("Refund me or I'm filing a chargeback", "escalate")


class Urgency(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(score_urgency("my account was compromised")[0], "critical")
        self.assertEqual(score_urgency("production is down for all users")[0], "high")
        self.assertEqual(score_urgency("I was charged twice")[0], "high")
        self.assertEqual(score_urgency("how do I change my email")[0], "low")
        self.assertEqual(score_urgency("please reset my password")[0], "normal")

    def test_critical_beats_low_signals_in_same_ticket(self):
        self.assertEqual(score_urgency("how do I know if I was hacked?")[0], "critical")


class Actions(unittest.TestCase):
    def test_action_called(self):
        r = handle_ticket("T0001", "Please reset my password")
        self.assertEqual(r["action"], "send_password_reset")
        self.assertIn("PWD-T0001", r["action_result"])

    def test_no_action_on_escalate(self):
        r = handle_ticket("T0002", "hello")
        self.assertIsNone(r["action"])
        self.assertIsNone(r["action_result"])

    def test_action_failure_escalates(self):
        from app import triage
        orig = triage.ACTIONS["password_reset"]
        def boom(_):
            raise RuntimeError("IdP timeout")
        boom.__name__ = "send_password_reset"
        triage.ACTIONS["password_reset"] = boom
        try:
            r = handle_ticket("T0003", "Please reset my password")
        finally:
            triage.ACTIONS["password_reset"] = orig
        self.assertEqual(r["decision"], "escalate")
        self.assertIn("IdP timeout", r["reason"])


class FakeClock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


class Queue(unittest.TestCase):
    def submit_text(self, store, text):
        return store.submit(text, *score_urgency(text))["id"]

    def drain(self, store):
        out = []
        while (t := store.next_ticket(timeout=0)) is not None:
            out.append(t["id"])
        return out

    def test_urgency_order_then_fifo(self):
        s = TicketStore(aging_seconds=0)
        low = self.submit_text(s, "how do I change my email")
        n1 = self.submit_text(s, "please reset my password")
        crit = self.submit_text(s, "my account was compromised")
        high = self.submit_text(s, "production is down, restart it")
        n2 = self.submit_text(s, "check my billing status")
        self.assertEqual(self.drain(s), [crit, high, n1, n2, low])

    def test_queue_position_reported(self):
        s = TicketStore(aging_seconds=0)
        self.submit_text(s, "how do I change my email")
        crit = self.submit_text(s, "my account was compromised")
        self.assertEqual(s.get(crit)["queue_position"], 1)

    def test_aging_prevents_starvation_but_not_past_critical(self):
        clock = FakeClock()
        s = TicketStore(aging_seconds=30, clock=clock)
        low = self.submit_text(s, "how do I change my email")
        clock.t += 61  # low has aged two levels -> high
        high = self.submit_text(s, "production is down")
        crit = self.submit_text(s, "my account was compromised")
        self.assertEqual(s.get(low)["effective_urgency"], "high")
        # critical still first; aged low beats the newer high by arrival order
        self.assertEqual(self.drain(s), [crit, low, high])

    def test_processed_order_and_complete(self):
        s = TicketStore(aging_seconds=0)
        a = self.submit_text(s, "please reset my password")
        t = s.next_ticket(timeout=0)
        self.assertEqual(t["processed_order"], 1)
        s.complete(a, handle_ticket(a, t["text"]))
        done = s.get(a)
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["category"], "password_reset")


if __name__ == "__main__":
    unittest.main()
