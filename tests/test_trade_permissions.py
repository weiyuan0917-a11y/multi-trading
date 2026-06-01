import os
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from api.services.trade_permissions import ensure_l3_confirmation, l3_confirmation_status
from config.user_env_store import save_user_env


class TestTradePermissions(unittest.TestCase):
    def setUp(self):
        self._old_env = dict(os.environ)
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old_env)
        self._td.cleanup()

    def _clear_l3_env(self):
        for key in (
            "OPENCLAW_MCP_MAX_LEVEL",
            "OPENCLAW_MCP_ALLOW_L3",
            "OPENCLAW_MCP_L3_CONFIRMATION_TOKEN",
        ):
            os.environ.pop(key, None)

    def test_owner_user_env_token_overrides_stale_process_token(self):
        os.environ["OPENCLAW_MCP_MAX_LEVEL"] = "L3"
        os.environ["OPENCLAW_MCP_ALLOW_L3"] = "true"
        os.environ["OPENCLAW_MCP_L3_CONFIRMATION_TOKEN"] = "stale-token"
        save_user_env(
            "alice",
            {
                "OPENCLAW_MCP_MAX_LEVEL": "L3",
                "OPENCLAW_MCP_ALLOW_L3": "true",
                "OPENCLAW_MCP_L3_CONFIRMATION_TOKEN": "current-token",
            },
            self.root,
        )

        ensure_l3_confirmation("current-token", owner_id="alice", root=self.root)

    def test_owner_user_env_rejects_mismatched_token(self):
        os.environ["OPENCLAW_MCP_MAX_LEVEL"] = "L3"
        os.environ["OPENCLAW_MCP_ALLOW_L3"] = "true"
        os.environ["OPENCLAW_MCP_L3_CONFIRMATION_TOKEN"] = "stale-token"
        save_user_env(
            "alice",
            {
                "OPENCLAW_MCP_MAX_LEVEL": "L3",
                "OPENCLAW_MCP_ALLOW_L3": "true",
                "OPENCLAW_MCP_L3_CONFIRMATION_TOKEN": "current-token",
            },
            self.root,
        )

        with self.assertRaises(HTTPException) as ctx:
            ensure_l3_confirmation("stale-token", owner_id="alice", root=self.root)

        self.assertEqual(403, ctx.exception.status_code)
        self.assertIn("confirmation_token", str(ctx.exception.detail))

    def test_unowned_worker_path_accepts_any_saved_user_token(self):
        self._clear_l3_env()
        save_user_env(
            "alice",
            {
                "OPENCLAW_MCP_MAX_LEVEL": "L3",
                "OPENCLAW_MCP_ALLOW_L3": "true",
                "OPENCLAW_MCP_L3_CONFIRMATION_TOKEN": "worker-token",
            },
            self.root,
        )

        ensure_l3_confirmation("worker-token", root=self.root)

    def test_status_reads_owner_user_env(self):
        self._clear_l3_env()
        save_user_env(
            "alice",
            {
                "OPENCLAW_MCP_MAX_LEVEL": "L3",
                "OPENCLAW_MCP_ALLOW_L3": "true",
                "OPENCLAW_MCP_L3_CONFIRMATION_TOKEN": "current-token",
            },
            self.root,
        )

        status = l3_confirmation_status(owner_id="alice", root=self.root)

        self.assertTrue(status["ready"])
        self.assertTrue(status["user_token_configured"])
        self.assertEqual("L3", status["max_level"])


if __name__ == "__main__":
    unittest.main()
