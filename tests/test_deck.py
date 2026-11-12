"""牌组按 apkg 文件名自动分层的行为测试。"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.exporter import deck_id_for, export_apkg, safe_filename  # noqa: E402
from server.models import Sentence  # noqa: E402
from server.pipeline import deck_name_for  # noqa: E402

TITLE = "The World's First Fitness Resort ($40M)"
SLUG = "The_World_s_First_Fitness_Resort_40M"
DECK = "Subs2Anki::" + SLUG


class TestDeckName(unittest.TestCase):
    def test_slug_matches_apkg_filename(self):
        self.assertEqual(safe_filename(TITLE), SLUG)

    def test_default_deck(self):
        self.assertEqual(deck_name_for(TITLE), DECK)

    def test_custom_root(self):
        self.assertEqual(deck_name_for("第1集", "MyDeck"), "MyDeck::第1集")
        self.assertEqual(deck_name_for(TITLE, "Subs2Anki::句子卡"),
                         "Subs2Anki::句子卡::" + SLUG)

    def test_empty_root_uses_slug(self):
        self.assertEqual(deck_name_for(TITLE, ""), SLUG)

    def test_deck_id_varies_by_name(self):
        self.assertNotEqual(deck_id_for("Subs2Anki::A"), deck_id_for("Subs2Anki::B"))
        self.assertEqual(deck_id_for(DECK), deck_id_for(DECK))


class TestApkgDeck(unittest.TestCase):
    def test_apkg_embeds_deck_name_and_filename_matches(self):
        with tempfile.TemporaryDirectory() as td:
            workdir = Path(td)
            out = export_apkg(
                [Sentence(1, 1.0, 2.0, "Hello world.")], workdir,
                "https://www.youtube.com/watch?v=x", TITLE, {}, DECK, "listen_first")
            self.assertEqual(out.stem, SLUG)
            with zipfile.ZipFile(out) as z:
                db = workdir / "check.anki2"
                db.write_bytes(z.read("collection.anki2"))
            con = sqlite3.connect(db)
            decks = json.loads(con.execute("select decks from col").fetchone()[0])
            names = [d["name"] for d in decks.values()]
            self.assertIn(DECK, names)
            con.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
