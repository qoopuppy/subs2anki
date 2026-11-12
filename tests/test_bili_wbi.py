"""B 站 WBI 签名的单元测试（不依赖网络）。"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import bili_wbi  # noqa: E402

IMG = "7cd08494133811aab7c1be4a6f9e8f83e5b"
SUB = "4932caff0ff746eab6f0bf2a0c2b9a1ff43"


class TestWbi(unittest.TestCase):
    def test_mixin_key_length_and_table(self):
        key = bili_wbi.mixin_key(IMG, SUB)
        self.assertEqual(len(key), 32)
        self.assertEqual(len(bili_wbi.MIXIN_KEY_ENC_TAB), 64)
        self.assertEqual(sorted(bili_wbi.MIXIN_KEY_ENC_TAB), list(range(64)))

    def test_sign_params_shape(self):
        key = bili_wbi.mixin_key(IMG, SUB)
        query = bili_wbi.sign_params({"bvid": "BV1ZT4b65Ezp", "cid": "43355604729"}, key)
        self.assertIn("bvid=BV1ZT4b65Ezp", query)
        self.assertIn("cid=43355604729", query)
        self.assertIn("wts=", query)
        self.assertRegex(query, r"w_rid=[0-9a-f]{32}$")
        # 参数按 key 排序：bvid < cid < wts
        self.assertLess(query.index("bvid="), query.index("cid="))
        self.assertLess(query.index("cid="), query.index("wts="))

    def test_sign_filters_special_chars(self):
        key = bili_wbi.mixin_key(IMG, SUB)
        query = bili_wbi.sign_params({"bvid": "BV'!(x)*", "cid": "1"}, key)
        self.assertNotIn("'", query)
        self.assertNotIn("!", query)
        self.assertIn("BVx", query)

    def test_signed_url_is_wbi_endpoint(self):
        with patch.object(bili_wbi, "wbi_keys", return_value=(IMG, SUB)):
            url = bili_wbi.signed_url("BV1ZT4b65Ezp", 43355604729)
        self.assertTrue(url.startswith("https://api.bilibili.com/x/player/wbi/v2?"))
        self.assertIn("w_rid=", url)
        self.assertIn("wts=", url)
        self.assertNotIn("/x/player/v2?", url)
        self.assertIsNotNone(re.search(r"w_rid=[0-9a-f]{32}$", url))

    def test_wbi_keys_cache(self):
        bili_wbi._CACHE["keys"] = (IMG, SUB)
        bili_wbi._CACHE["ts"] = bili_wbi.time.time()
        with patch.object(bili_wbi, "_fetch_json") as fetch:
            self.assertEqual(bili_wbi.wbi_keys(), (IMG, SUB))
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
