import unittest
import os
import sys
from types import SimpleNamespace

from fastapi import HTTPException

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MCP_SERVER_DIR = os.path.join(_ROOT, "mcp_server")
if _MCP_SERVER_DIR not in sys.path:
    sys.path.insert(0, _MCP_SERVER_DIR)

from api.runtime_bridge import _broker_credentials_from_setup_body
from api.schemas_setup import SetupAccountRegisterBody


class TestSetupBrokerCredentials(unittest.TestCase):
    def test_tiger_credentials_are_mapped_to_generic_broker_credentials(self):
        fake_main = SimpleNamespace(HTTPException=HTTPException)

        parsed = SetupAccountRegisterBody.model_validate(
            {
                "account_id": "paper",
                "broker_provider": "tiger",
                "credentials": {
                    "tiger_id": "20150001",
                    "account": "PAPER-001",
                    "license": "abc-license",
                    "env": "PAPER",
                    "private_key_path": "D:/keys/tiger.pem",
                    "token_path": "D:/keys/token.txt",
                },
            }
        )

        creds = _broker_credentials_from_setup_body(fake_main, parsed, "tiger")

        self.assertEqual("20150001", creds.app_key)
        self.assertEqual("abc-license", creds.app_secret)
        self.assertEqual("PAPER-001", creds.access_token)
        self.assertEqual("PAPER", creds.extras["env"])
        self.assertEqual("D:/keys/tiger.pem", creds.extras["private_key_path"])
        self.assertEqual("D:/keys/token.txt", creds.extras["token_path"])

    def test_tiger_credentials_require_private_key_source(self):
        fake_main = SimpleNamespace(HTTPException=HTTPException)
        parsed = SetupAccountRegisterBody.model_validate(
            {
                "account_id": "paper",
                "broker_provider": "tiger",
                "credentials": {
                    "tiger_id": "20150001",
                    "account": "PAPER-001",
                    "license": "abc-license",
                },
            }
        )

        with self.assertRaises(HTTPException) as ctx:
            _broker_credentials_from_setup_body(fake_main, parsed, "tiger")

        self.assertEqual(400, ctx.exception.status_code)
        self.assertIn("private_key", str(ctx.exception.detail))


if __name__ == "__main__":
    unittest.main()
