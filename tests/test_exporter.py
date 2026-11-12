"""卡片正面倍速控件与导出字段的单元测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.exporter import (CSS, PLAYBACK_RATES, audio_widget, make_fields,
                             render_tsv)
from server.models import Sentence


class TestAudioWidget(unittest.TestCase):
    def test_rate_ladder(self):
        self.assertEqual(PLAYBACK_RATES, [0.5, 0.75, 1.0, 1.5, 2.0])

    def test_all_buttons_present(self):
        w = audio_widget("s2a_0001.mp3")
        for rate in PLAYBACK_RATES:
            self.assertIn(f'data-rate="{rate:g}"', w)
            self.assertIn(f"{rate:g}×</button>", w)

    def test_audio_element_and_native_fallback(self):
        w = audio_widget("s2a_0001.mp3")
        self.assertIn('<audio class="s2a-audio" src="s2a_0001.mp3"', w)
        self.assertIn("[sound:s2a_0001.mp3]", w)

    def test_script_is_single_line_for_tsv(self):
        # TSV 会把字段里的换行压成空格，脚本不能依赖换行分隔语句
        w = audio_widget("s2a_0001.mp3")
        self.assertNotIn("\n", w)
        self.assertIn("playbackRate", w)
        self.assertIn("s2aPlay", w)
        self.assertIn("localStorage", w)

    def test_default_rate_highlighted(self):
        w = audio_widget("s2a_0001.mp3")
        self.assertIn('class="s2a-rate s2a-on" data-rate="1"', w)

    def test_css_has_rate_styles(self):
        self.assertIn(".s2a-rate", CSS)
        self.assertIn(".s2a-wrap", CSS)


class TestMakeFields(unittest.TestCase):
    def setUp(self):
        self.sent = Sentence(1, 1.2, 4.2, "おはようございます。")
        self.url = "https://www.youtube.com/watch?v=abc"

    def test_with_audio_has_widget_and_link(self):
        f = make_fields(self.sent, self.url, "第1集", {1: "s2a_0001.mp3"})
        self.assertIn("s2a-wrap", f["Audio"])
        self.assertIn("0.75×", f["Audio"])
        self.assertIn("t=1s", f["Audio"])

    def test_without_audio_has_no_widget(self):
        f = make_fields(self.sent, self.url, "第1集", {})
        self.assertNotIn("s2a-wrap", f["Audio"])
        self.assertNotIn("<audio", f["Audio"])
        self.assertIn("t=1s", f["Audio"])

    def test_tsv_front_carries_the_widget(self):
        tsv = render_tsv([self.sent], self.url, "第1集", {1: "s2a_0001.mp3"}, "Deck")
        rows = [ln for ln in tsv.splitlines() if not ln.startswith("#")]
        self.assertEqual(len(rows), 1)
        front = rows[0].split("\t")[0]
        self.assertIn("s2aPlay", front)
        self.assertIn('data-rate="1.5"', front)
        self.assertIn("[sound:s2a_0001.mp3]", front)


if __name__ == "__main__":
    unittest.main(verbosity=2)
