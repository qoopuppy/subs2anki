"""音频下载：代理选择与错误信息回传的单元测试。"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import media  # noqa: E402


class TestDownloadAudio(unittest.TestCase):
    def setUp(self):
        self.cfg = {"proxy": "http://127.0.0.1:7890", "yt_dlp": "yt-dlp"}
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, url: str, alive: bool = True) -> tuple[list[str], str]:
        with patch.object(media, "ytdlp_command", return_value=["yt-dlp"]), \
             patch.object(media, "_proxy_alive", return_value=alive), \
             patch.object(media, "_run") as run:
            run.return_value = subprocess.CompletedProcess([], 1, "", "ERROR: proxy 502")
            _, err = media.download_audio(url, self.workdir, self.cfg)
            return run.call_args[0][0], err

    def test_resolve_proxy_from_config(self):
        self.assertEqual(media.resolve_proxy({"proxy": "http://127.0.0.1:7890"}),
                         "http://127.0.0.1:7890")

    def test_youtube_uses_system_proxy(self):
        cmd, _ = self._run("https://www.youtube.com/watch?v=jNQXAC9IVRw")
        self.assertIn("--proxy", cmd)
        self.assertIn("http://127.0.0.1:7890", cmd)

    def test_bilibili_does_not_use_proxy(self):
        cmd, _ = self._run("https://www.bilibili.com/video/BV1ZT4b65Ezp/?p=2")
        self.assertNotIn("--proxy", cmd)

    def test_dead_proxy_is_skipped(self):
        cmd, _ = self._run("https://www.youtube.com/watch?v=jNQXAC9IVRw", alive=False)
        self.assertNotIn("--proxy", cmd)

    def test_error_message_is_returned(self):
        _, err = self._run("https://www.youtube.com/watch?v=jNQXAC9IVRw")
        self.assertIn("proxy 502", err)

    def test_no_ytdlp_reports_reason(self):
        with patch.object(media, "ytdlp_command", return_value=[]):
            path, err = media.download_audio("https://example.com", self.workdir, self.cfg)
        self.assertIsNone(path)
        self.assertIn("yt-dlp", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
