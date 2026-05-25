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

    def test_fosun_credentials_are_mapped_to_sdk_credentials(self):
        fake_main = SimpleNamespace(HTTPException=HTTPException)
        parsed = SetupAccountRegisterBody.model_validate(
            {
                "account_id": "fosun-main",
                "broker_provider": "fosun",
                "credentials": {
                    "api_key": "fs-api-key",
                    "base_url": "https://openapi.example.com",
                    "sub_account_id": "SUB-001",
                    "server_public_key": "-----BEGIN PUBLIC KEY-----\\nSERVER\\n-----END PUBLIC KEY-----",
                    "client_private_key": "-----BEGIN PRIVATE KEY-----\\nCLIENT\\n-----END PRIVATE KEY-----",
                    "sdk_type": "ops",
                    "option_apply_account_id": "OPT-001",
                },
            }
        )

        creds = _broker_credentials_from_setup_body(fake_main, parsed, "fosun")

        self.assertEqual("fs-api-key", creds.app_key)
        self.assertEqual("SUB-001", creds.access_token)
        self.assertEqual("https://openapi.example.com", creds.extras["base_url"])
        self.assertEqual("ops", creds.extras["sdk_type"])
        self.assertEqual("OPT-001", creds.extras["option_apply_account_id"])
        self.assertIn("SERVER", creds.extras["server_public_key"])
        self.assertIn("CLIENT", creds.extras["client_private_key"])

    def test_fosun_credentials_require_sdk_keys(self):
        fake_main = SimpleNamespace(HTTPException=HTTPException)
        parsed = SetupAccountRegisterBody.model_validate(
            {
                "account_id": "fosun-main",
                "broker_provider": "fosun",
                "credentials": {
                    "api_key": "fs-api-key",
                    "base_url": "https://openapi.example.com",
                    "sub_account_id": "SUB-001",
                },
            }
        )

        with self.assertRaises(HTTPException) as ctx:
            _broker_credentials_from_setup_body(fake_main, parsed, "fosun")

        self.assertEqual(400, ctx.exception.status_code)
        self.assertIn("server_public_key/client_private_key", str(ctx.exception.detail))


if __name__ == "__main__":
    unittest.main()
