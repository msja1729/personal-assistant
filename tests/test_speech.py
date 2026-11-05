"""Seamless speech: sentence splitting, cleanup, and the gap between spoken sentences."""

import numpy as np
import pytest

from src.output import clean_for_speech, split_sentences
from src.platform_support import Speaker, prepare_chunk


def test_split_complete_sentences():
    sentences, rest = split_sentences("Hello there. How are you? I am fi")
    assert sentences == ["Hello there.", "How are you?"]
    assert rest == "I am fi"


def test_split_keeps_abbreviations_and_numbers():
    sentences, rest = split_sentences("Use a tool, e.g. a hammer. 2. Then nail it")
    assert sentences == ["Use a tool, e.g. a hammer."]
    assert rest == "2. Then nail it"


def test_streaming_pieces_join_up():
    buf, out = "", []
    for piece in ["The ans", "wer is 42", ". Next", " one! Done"]:
        done, buf = split_sentences(buf + piece)
        out += done
    assert out == ["The answer is 42.", "Next one!"]
    assert buf == "Done"


def test_clean_for_speech():
    text = "## Result\n**Bold** and `code` see [docs](https://x.y) and https://a.b/c\n- item one"
    assert clean_for_speech(text) == "Result Bold and code see docs and a link item one"


def test_prepare_chunk_trims_and_pads():
    rate = 22050
    silence = np.zeros(rate, dtype=np.int16)  # 1 s of silence either side
    voice = (np.sin(np.linspace(0, 400, rate // 2)) * 8000).astype(np.int16)
    chunk = prepare_chunk(np.concatenate([silence, voice, silence]), rate, 0.03, 0.006, 0.17)
    expected = len(voice) + 2 * int(rate * 0.03) + int(rate * 0.17)
    assert abs(len(chunk) - expected) < rate * 0.01
    assert chunk[0] == 0  # faded in: no click
    assert np.all(chunk[-int(rate * 0.17):] == 0)


def _silences_ms(audio: np.ndarray, rate: int) -> list[int]:
    win = int(rate * 0.01)
    levels = [np.sqrt(np.mean((audio[i:i + win] / 32768.0) ** 2)) for i in range(0, len(audio) - win, win)]
    gaps, run, started = [], 0, False
    for level in levels:
        if level >= 0.003:
            if started and run:
                gaps.append(run * 10)
            started, run = True, 0
        else:
            run += 1
    return gaps


def test_gap_between_spoken_sentences_is_short():
    """Spec: silence between consecutive sentences is at most 300 ms."""
    chunks = []
    speaker = Speaker(sink=chunks.append)
    if not speaker.available:
        pytest.skip("no OS speech engine on this machine")
    for s in ["This is the first sentence.", "Here comes the second one.", "And this is the last."]:
        speaker.say(s)
    assert speaker.wait_until_done(60)
    assert len(chunks) == 3
    audio = np.concatenate(chunks).astype(np.float32)
    gaps = _silences_ms(audio, Speaker.RATE)
    assert gaps, "no speech was produced"
    assert max(gaps) <= 300, gaps


def test_stop_drops_queued_sentences():
    chunks = []
    speaker = Speaker(sink=chunks.append)
    speaker.stop()
    assert not speaker.is_speaking()
    assert speaker.wait_until_done(0.1)
