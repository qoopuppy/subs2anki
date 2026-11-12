"""断句与字幕解析的单元测试：python -m unittest discover -s tests -v"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.models import (Cue, normalize_cues, parse_bilibili_json,
                           parse_srt, parse_vtt, parse_youtube_json3,
                           timestamp_url)
from server.segmenter import SegmentConfig, attach_translation, guess_lang, segment


class TestParsers(unittest.TestCase):
    def test_srt(self):
        text = "1\n00:00:01,200 --> 00:00:02,600\nおはよう、\n\n2\n00:00:02,700 --> 00:00:04,200\n今日はいい天気ですね。\n"
        cues = parse_srt(text)
        self.assertEqual(len(cues), 2)
        self.assertAlmostEqual(cues[0].start, 1.2)
        self.assertEqual(cues[1].text, "今日はいい天気ですね。")

    def test_vtt(self):
        text = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000 align:start\n<c>Hello</c> world\n\n00:00:02.100 --> 00:00:03.000\nHow are you?\n"
        cues = parse_vtt(text)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0].text, "Hello world")

    def test_youtube_json3(self):
        text = '{"events":[{"tStartMs":1000,"dDurationMs":1500,"segs":[{"utf8":"Hello "}]},{"tStartMs":2500,"dDurationMs":1200,"aAppend":1,"segs":[{"utf8":"world."}]}]}'
        cues = parse_youtube_json3(text)
        self.assertEqual(len(cues), 1)
        self.assertIn("Hello", cues[0].text)
        self.assertIn("world", cues[0].text)

    def test_bilibili_json(self):
        text = '{"body":[{"from":1.0,"to":2.5,"location":2,"content":"你好"},{"from":2.6,"to":4.0,"location":2,"content":"世界"}]}'
        cues = parse_bilibili_json(text)
        self.assertEqual([c.text for c in cues], ["你好", "世界"])

    def test_normalize_dedup(self):
        cues = normalize_cues([
            {"start": 2.0, "end": 3.0, "text": "B"},
            {"start": 1.0, "end": 2.0, "text": "A"},
            {"start": 1.0, "end": 2.0, "text": "A"},
        ])
        self.assertEqual([c.text for c in cues], ["A", "B"])


class TestSegmenter(unittest.TestCase):
    def test_merges_fragments_into_sentence(self):
        cues = [
            Cue(1.0, 2.0, "おはよう、"),
            Cue(2.1, 3.8, "今日はいい天気ですね。"),
            Cue(5.0, 6.5, "朝ごはんはもう食べましたか？"),
        ]
        sentences = segment(cues, SegmentConfig(lang="ja"))
        self.assertGreaterEqual(len(sentences), 2)
        self.assertIn("おはよう", sentences[0].text)
        self.assertIn("天気", sentences[0].text)
        self.assertNotIn("朝ごはん", sentences[0].text)

    def test_no_half_sentences(self):
        cues = [Cue(1.0, 2.0, "I "), Cue(2.1, 3.0, "think "),
                Cue(3.1, 4.5, "this is great."), Cue(6.0, 7.5, "Really?")]
        sentences = segment(cues, SegmentConfig(lang="en", gap_threshold=0.8))
        self.assertEqual(len(sentences), 2)
        self.assertEqual(sentences[0].text, "I think this is great.")

    def test_max_duration_forces_break(self):
        cues = [Cue(i * 2.0, i * 2.0 + 1.9, f"word{i}") for i in range(10)]
        sentences = segment(cues, SegmentConfig(lang="en", max_duration=5.0))
        self.assertGreaterEqual(len(sentences), 3)

    def test_cjk_join_without_spaces(self):
        cues = [Cue(1, 2, "今日は"), Cue(2.1, 3.5, "晴れです。")]
        self.assertEqual(segment(cues, SegmentConfig(lang="ja"))[0].text, "今日は晴れです。")

    def test_lang_guess(self):
        self.assertEqual(guess_lang([Cue(0, 1, "こんにちは世界")]), "ja")
        self.assertEqual(guess_lang([Cue(0, 1, "안녕하세요")]), "ko")

    def test_translation_attachment(self):
        cues = [Cue(1.0, 3.0, "Hello there.")]
        sents = segment(cues, SegmentConfig(lang="en"))
        attach_translation(sents, [Cue(1.0, 3.0, "你好呀。")])
        self.assertEqual(sents[0].translation, "你好呀。")

    def test_timestamp_url(self):
        self.assertIn("t=12s", timestamp_url("https://www.youtube.com/watch?v=abc", 12.4))
        self.assertIn("t=12", timestamp_url("https://www.bilibili.com/video/BV123?p=2", 12.4))


if __name__ == "__main__":
    unittest.main(verbosity=2)
