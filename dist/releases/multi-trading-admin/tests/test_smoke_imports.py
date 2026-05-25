import importlib
import unittest


class TestSmokeImports(unittest.TestCase):
    def test_trade_safety_imports_without_broker_sdk(self):
        mod = importlib.import_module("api.services.trade_safety")
        self.assertTrue(hasattr(mod, "guard_before_submit_order"))

    def test_schemas_import_without_broker_sdk(self):
        mod = importlib.import_module("api.schemas_options_trade")
        self.assertTrue(hasattr(mod, "SubmitOrderBody"))


if __name__ == "__main__":
    unittest.main()
