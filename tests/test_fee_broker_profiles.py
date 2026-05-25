import json
import tempfile
import unittest
from pathlib import Path

from api.services import fee_broker_profiles as profiles
from mcp_server.fee_model import get_default_fee_schedule


class FeeBrokerProfilesTests(unittest.TestCase):
    def test_init_injects_builtin_fosun_profile(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "fee_schedule.json"
            path.write_text(
                json.dumps(
                    {
                        "format": "multi_broker_v1",
                        "active_broker_id": "longbridge",
                        "manual_fee_broker_id": "longbridge",
                        "brokers": {
                            "longbridge": {
                                "display_name": "长桥（默认）",
                                "schedule": get_default_fee_schedule(),
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            profiles.init_fee_broker_profiles(str(path))

            listed = profiles.list_broker_profiles()
            by_id = {b["broker_id"]: b["display_name"] for b in listed["brokers"]}
            self.assertEqual(by_id["fosun"], "复兴证券")
            self.assertEqual(by_id["tiger"], "老虎")
            self.assertEqual(listed["manual_fee_broker_id"], "longbridge")
