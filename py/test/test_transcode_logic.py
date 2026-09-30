"""Pure-logic tests for py/ui/transcode.py (no ffmpeg needed). Run: python test_transcode_logic.py"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "py"))

from ui.transcode import build_ffmpeg_cmd, dest_for, parse_probe, suggest_audio  # noqa: E402


def _a(index, language="", title="", **dispo):
    return {"index": index, "language": language, "title": title,
            "comment": False, "hearing_impaired": False, "visual_impaired": False, **dispo}


class SuggestAudioTests(unittest.TestCase):
    def test_first_clean_english(self):
        self.assertEqual(suggest_audio([_a(1, "dan"), _a(2, "eng"), _a(3, "eng")]), 2)

    def test_rejects_commentary_and_description_titles(self):
        audio = [_a(1, "eng", "Director's Commentary"), _a(2, "eng", "English - Audio Description"),
                 _a(3, "eng", "English SDH"), _a(4, "eng", "English")]
        self.assertEqual(suggest_audio(audio), 4)

    def test_rejects_dispositions(self):
        audio = [_a(1, "eng", comment=True), _a(2, "eng", visual_impaired=True),
                 _a(3, "eng", hearing_impaired=True)]
        self.assertIsNone(suggest_audio(audio))

    def test_undefined_language_with_english_title(self):
        self.assertEqual(suggest_audio([_a(5, "und", "English 5.1")]), 5)

    def test_language_variants(self):
        self.assertEqual(suggest_audio([_a(1, "en-US")]), 1)
        self.assertEqual(suggest_audio([_a(1, "EN")]), 1)

    def test_no_english(self):
        self.assertIsNone(suggest_audio([_a(1, "fre"), _a(2, "ger")]))

    def test_language_priority_order(self):
        audio = [_a(1, "eng"), _a(2, "dan")]
        self.assertEqual(suggest_audio(audio, ["dan", "eng"]), 2)
        self.assertEqual(suggest_audio([_a(1, "fre"), _a(2, "eng")], ["dan", "eng"]), 2)

    def test_two_and_three_letter_codes_equivalent(self):
        self.assertEqual(suggest_audio([_a(3, "deu")], ["de"]), 3)
        self.assertEqual(suggest_audio([_a(4, "da")], ["dan"]), 4)
        self.assertEqual(suggest_audio([_a(5, "und", "Danish 5.1")], ["dan"]), 5)

    def test_custom_reject_words(self):
        audio = [_a(1, "eng", "Stereo"), _a(2, "eng", "Surround")]
        self.assertEqual(suggest_audio(audio, ["eng"], ["stereo"]), 2)

    def test_prefer_default_and_channels(self):
        audio = [dict(_a(1, "eng"), channels=2), dict(_a(2, "eng"), channels=6, default=True)]
        self.assertEqual(suggest_audio(audio, ["eng"], prefer="first"), 1)
        self.assertEqual(suggest_audio(audio, ["eng"], prefer="default"), 2)
        self.assertEqual(suggest_audio(audio, ["eng"], prefer="channels"), 2)


class ParseProbeTests(unittest.TestCase):
    def test_parse(self):
        data = {
            "format": {"duration": "5400.5", "size": "123"},
            "streams": [
                {"index": 0, "codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
                {"index": 1, "codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
                {"index": 2, "codec_type": "audio", "codec_name": "eac3", "channels": 6,
                 "tags": {"language": "eng", "title": "English"}, "disposition": {"default": 1}},
                {"index": 3, "codec_type": "subtitle"},
                {"index": 4, "codec_type": "subtitle"},
            ],
        }
        p = parse_probe(data)
        self.assertEqual(p["duration"], 5400.5)
        self.assertEqual(p["size"], 123)
        self.assertEqual(p["video"]["codec"], "h264")
        self.assertEqual(p["subtitles"], 2)
        self.assertEqual(p["audio"][0]["index"], 2)
        self.assertTrue(p["audio"][0]["default"])

    def test_empty(self):
        p = parse_probe({})
        self.assertEqual((p["duration"], p["video"], p["audio"]), (0.0, None, []))


class DestForTests(unittest.TestCase):
    def test_movie_folder(self):
        item = r"D:\Downloaded\Netflix\Some Movie (2020)"
        dst = dest_for(item + r"\Some.Movie.mp4", item, item, r"E:\Transcoded")
        self.assertEqual(dst, Path(r"E:\Transcoded\Movies\Some Movie (2020)\Some.Movie.mkv"))

    def test_movie_folder_subdir_kept(self):
        item = r"M:\Movies\Film (1999)"
        dst = dest_for(item + r"\Disc 1\film.avi", item, item, r"E:\T")
        self.assertEqual(dst, Path(r"E:\T\Movies\Film (1999)\Disc 1\film.mkv"))

    def test_loose_file_gets_own_folder(self):
        src = r"D:\Downloaded\Netflix\Loose Movie (2021).mkv"
        dst = dest_for(src, src, r"D:\Downloaded\Netflix", r"E:\T")
        self.assertEqual(dst, Path(r"E:\T\Movies\Loose Movie (2021)\Loose Movie (2021).mkv"))


class FfmpegCmdTests(unittest.TestCase):
    def test_amf(self):
        cmd = build_ffmpeg_cmd("ffmpeg", "in.mp4", "out.mkv", 3, "hevc_amf", 22)
        self.assertIn("0:3", cmd)
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "hevc_amf")
        self.assertIn("-qp_i", cmd)
        self.assertEqual(cmd[-1], "out.mkv")

    def test_x265_uses_crf(self):
        cmd = build_ffmpeg_cmd("ffmpeg", "in.mkv", "out.mkv", 1, "libx265", 20)
        self.assertEqual(cmd[cmd.index("-crf") + 1], "20")


if __name__ == "__main__":
    unittest.main()
