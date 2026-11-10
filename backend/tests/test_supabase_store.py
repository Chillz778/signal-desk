"""The optional persistence adapter keeps credentials out of requests/logs."""

import unittest

import httpx

from app.database.supabase_store import StorageError, SupabaseStore


class SupabaseStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_secret_key_is_sent_only_as_apikey(self) -> None:
        observed = {}

        def handler(request: httpx.Request) -> httpx.Response:
            observed["path"] = request.url.path
            observed["apikey"] = request.headers.get("apikey")
            observed["authorization"] = request.headers.get("authorization")
            return httpx.Response(200, json=[])

        store = SupabaseStore(
            "https://example.supabase.co",
            "sb_secret_test-secret",
            transport=httpx.MockTransport(handler),
        )
        result = await store.get_strategy_status("trend_following", "1.0.0", "BTCUSD")
        self.assertIsNone(result)
        self.assertEqual(observed["path"], "/rest/v1/strategy_status")
        self.assertEqual(observed["apikey"], "sb_secret_test-secret")
        self.assertIsNone(observed["authorization"])

    async def test_pending_alerts_include_new_developing_live_signals(self) -> None:
        observed = {}

        def handler(request: httpx.Request) -> httpx.Response:
            observed["mode"] = request.url.params.get("mode")
            observed["status"] = request.url.params.get("status")
            return httpx.Response(200, json=[])

        store = SupabaseStore(
            "https://example.supabase.co",
            "sb_secret_test-secret",
            transport=httpx.MockTransport(handler),
        )
        self.assertEqual(await store.list_pending_alerts(), [])
        self.assertEqual(observed["mode"], "eq.live")
        self.assertEqual(observed["status"], "in.(developing,confirmed)")

    async def test_failed_write_does_not_echo_secret_or_provider_body(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="internal failure and credential details")

        key = "sb_secret_never-print-this"
        store = SupabaseStore(
            "https://example.supabase.co",
            key,
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(StorageError) as raised:
            await store.insert_signal({"signal_id": "abc123"})
        self.assertNotIn(key, str(raised.exception))
        self.assertNotIn("credential details", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
