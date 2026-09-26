"""Voucher tests use synthetic inventory in temporary databases only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from uuid import uuid4

import rewards


class TestStore:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE users(id TEXT PRIMARY KEY);
                CREATE TABLE points_ledger(id INTEGER PRIMARY KEY,user_id TEXT REFERENCES users(id),delta INTEGER NOT NULL);
            """)
        rewards.initialize_rewards(self)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()


class RewardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TestStore(Path(self.temp.name) / "rewards-test.sqlite3")
        self.user = self.account(100)
        self.other = self.account(100)

    def tearDown(self):
        self.temp.cleanup()

    def account(self, points):
        user_id = str(uuid4())
        with self.store.connect() as db:
            db.execute("INSERT INTO users(id) VALUES(?)", (user_id,))
            db.execute("INSERT INTO points_ledger(user_id,delta) VALUES(?,?)", (user_id, points))
        return user_id

    def stock(self, count=1, points=50, enabled=True, expiry=None):
        rewards.configure_reward(self.store, "test-reward", "Test issuer", "Test voucher", points, "Test value", enabled)
        rewards.import_inventory(self.store, "test-reward", [
            {"code": f"TEST-ONLY-{uuid4()}", "expires_at": expiry} for _ in range(count)
        ], "Test operator")

    def redeem(self, user=None, request_id=None):
        return rewards.redeem(self.store, user or self.user, "test-reward", request_id or str(uuid4()))

    def test_seed_catalog_is_inactive_and_does_not_promise_values(self):
        data = rewards.catalog(self.store)
        self.assertEqual({r["brand"] for r in data}, {"New World", "Woolworths", "Farmers", "Chemist Warehouse"})
        self.assertTrue(all(r["status"] == "unavailable" and not r["available"] for r in data))
        self.assertTrue(all(r["points_cost"] is None and r["value_label"] is None for r in data))
        rewards.initialize_rewards(self.store)
        self.assertEqual(len(rewards.catalog(self.store)), 4)

    def test_redemption_debits_once_and_only_owner_can_read_code(self):
        self.stock()
        request_id = str(uuid4())
        first = self.redeem(request_id=request_id)
        again = self.redeem(request_id=request_id)
        self.assertEqual(first, again)
        self.assertEqual(rewards.points_balance(self.store, self.user), 50)
        self.assertEqual(rewards.mine(self.store, self.user), [first])
        self.assertEqual(rewards.mine(self.store, self.other), [])
        self.assertNotIn(first["voucher_code"], json.dumps(rewards.catalog(self.store)))
        self.assertEqual(rewards.points_balance(self.store, self.other), 100)

    def test_idempotency_key_is_bound_to_account_and_reward(self):
        self.stock()
        request_id = str(uuid4())
        self.redeem(request_id=request_id)
        with self.assertRaisesRegex(ValueError, "already been used"):
            self.redeem(user=self.other, request_id=request_id)
        with self.assertRaisesRegex(ValueError, "already been used"):
            rewards.redeem(self.store, self.user, "farmers", request_id)
        self.assertEqual(rewards.points_balance(self.store, self.other), 100)

    def test_invalid_id_or_missing_account_cannot_redeem(self):
        self.stock()
        with self.assertRaisesRegex(ValueError, "request ID"):
            self.redeem(request_id="bad-id")
        with self.assertRaisesRegex(ValueError, "Sign in"):
            self.redeem(user=str(uuid4()))
        self.assertEqual(rewards.points_balance(self.store, self.user), 100)

    def test_unavailable_and_empty_rewards_never_charge(self):
        self.stock(enabled=False)
        with self.assertRaisesRegex(ValueError, "not available"):
            self.redeem()
        rewards.configure_reward(self.store, "empty", "Test", "Empty", 5, "Test", True)
        with self.assertRaisesRegex(ValueError, "out of stock"):
            rewards.redeem(self.store, self.user, "empty", str(uuid4()))
        self.assertEqual(rewards.points_balance(self.store, self.user), 100)

    def test_insufficient_funds_leave_inventory_available(self):
        self.stock(points=101)
        with self.assertRaisesRegex(ValueError, "enough points"):
            self.redeem()
        self.assertEqual(rewards.points_balance(self.store, self.user), 100)
        self.assertEqual(rewards.mine(self.store, self.user), [])
        self.assertTrue(next(r for r in rewards.catalog(self.store) if r["id"] == "test-reward")["available"])

    def test_expired_voucher_cannot_be_allocated(self):
        self.stock()
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        with self.store.connect() as db:
            db.execute("UPDATE reward_vouchers SET expires_at=?", (past,))
        with self.assertRaisesRegex(ValueError, "out of stock"):
            self.redeem()
        self.assertEqual(rewards.points_balance(self.store, self.user), 100)

    def test_last_voucher_is_only_allocated_to_one_account(self):
        self.stock()
        barrier = threading.Barrier(2)

        def worker(user_id):
            barrier.wait()
            try:
                return self.redeem(user=user_id)
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(worker, [self.user, self.other]))
        self.assertEqual(sum(outcome is not None for outcome in outcomes), 1)
        self.assertEqual(sorted([rewards.points_balance(self.store, self.user), rewards.points_balance(self.store, self.other)]), [50, 100])

    def test_concurrent_redemptions_cannot_overspend_an_account(self):
        self.stock(count=2, points=75)
        barrier = threading.Barrier(2)

        def worker(_):
            barrier.wait()
            try:
                return self.redeem()
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(worker, range(2)))
        self.assertEqual(sum(outcome is not None for outcome in outcomes), 1)
        self.assertEqual(rewards.points_balance(self.store, self.user), 25)

    def test_concurrent_retries_allocate_and_debit_once(self):
        self.stock(count=2)
        request_id = str(uuid4())
        barrier = threading.Barrier(2)

        def worker(_):
            barrier.wait()
            return self.redeem(request_id=request_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(worker, range(2)))
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(rewards.points_balance(self.store, self.user), 50)

    def test_import_is_atomic_and_rejects_duplicates_without_echoing_codes(self):
        self.stock()
        with self.store.connect() as db:
            old = db.execute("SELECT code FROM reward_vouchers").fetchone()[0]
        with self.assertRaises(ValueError) as caught:
            rewards.import_inventory(self.store, "test-reward", [
                {"code": "TEST-ONLY-NEW", "expires_at": None}, {"code": old, "expires_at": None}
            ], "Test operator")
        self.assertNotIn(old, str(caught.exception))
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reward_vouchers").fetchone()[0], 1)

    def test_import_requires_explicit_valid_expiry_and_actual_reward(self):
        self.stock()
        for expiry in ["2020-01-01T00:00:00Z", "2099-01-01", 5]:
            with self.assertRaises(ValueError):
                rewards.import_inventory(self.store, "test-reward", [{"code": "TEST-ONLY-EXPIRY", "expires_at": expiry}], "Test")
        with self.assertRaises(ValueError):
            rewards.import_inventory(self.store, "test-reward", [{"code": "TEST-ONLY-MISSING"}], "Test")
        with self.assertRaises(ValueError):
            rewards.import_inventory(self.store, "does-not-exist", [{"code": "TEST-ONLY-UNKNOWN", "expires_at": None}], "Test")

    def test_history_keeps_price_and_value_snapshot_after_catalog_edit(self):
        self.stock()
        receipt = self.redeem()
        rewards.configure_reward(self.store, "test-reward", "Test issuer", "Renamed voucher", 75, "Test value", False)
        self.assertEqual(rewards.mine(self.store, self.user), [receipt])
        with self.assertRaisesRegex(ValueError, "new reward ID"):
            rewards.configure_reward(self.store, "test-reward", "Test issuer", "Test voucher", 75, "Different value", True)

    def test_foreign_keys_prevent_orphan_redemption_and_inventory(self):
        self.stock()
        self.redeem()
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.connect() as db:
                db.execute("DELETE FROM users WHERE id=?", (self.user,))
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.connect() as db:
                db.execute("DELETE FROM reward_vouchers")

    def test_review_revocation_preserves_negative_balance_without_free_redemptions(self):
        self.stock(count=2, points=75)
        self.redeem()
        with self.store.connect() as db:
            db.execute("INSERT INTO points_ledger(user_id,delta) VALUES(?,-100)", (self.user,))
        self.assertEqual(rewards.points_balance(self.store, self.user), -75)
        with self.assertRaisesRegex(ValueError, "enough points"):
            self.redeem()


if __name__ == "__main__":
    unittest.main()
