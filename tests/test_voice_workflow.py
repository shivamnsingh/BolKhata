"""Regression tests for the voice transaction workflow (server side of the browser protocol)."""
import wave

import pytest

from bolkhata import voice_workflow as vw
from bolkhata.database import connect
from bolkhata.ledger import LedgerError, balance_paise, list_customers, list_transactions
from bolkhata.prisma_client import PrismaError

RAMESH = "Ramesh ke khate mein 70 rupaye udhaar likh do."
SURESH = "Suresh ne 100 rupaye de diye."


def make_wav(path, seconds=1.0, rate=16000):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))
    return path.read_bytes()


@pytest.fixture
def db():
    connection = connect(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def wav_bytes(tmp_path):
    return make_wav(tmp_path / "ok.wav")


def event(event_id, audio, mime="audio/wav", **extra):
    return {"event_id": event_id, "audio_bytes": audio, "mime_type": mime, "duration_ms": 1000, **extra}


class FakeStt:
    def __init__(self, *transcripts):
        self.transcripts = list(transcripts)
        self.calls = 0

    def __call__(self, path, language_code="hi-IN,en-IN", mime_type=None, **_):
        self.calls += 1
        return {"success": True, "transcript": self.transcripts.pop(0)}


def save(state, db, name, amount, kind):
    return vw.confirm_pending(state, db, name, amount, kind, "")


def test_event_is_not_acknowledged_or_saved_until_confirmed(db, wav_bytes):
    state, stt = {}, FakeStt(RAMESH)
    assert vw.handle_event(state, event("e1", wav_bytes), stt) == "pending"
    assert vw.pending(state) == ("e1", RAMESH)
    assert state.get(vw.ACK_EVENT) is None  # browser must keep waiting during review
    assert list_transactions(db) == []


def test_confirm_saves_once_then_acknowledges(db, wav_bytes):
    state = {}
    vw.handle_event(state, event("e1", wav_bytes), FakeStt(RAMESH))
    save(state, db, "Ramesh", 70, "credit")
    assert state[vw.ACK_EVENT] == "e1"
    assert vw.pending(state) is None
    [txn] = list_transactions(db)
    assert (txn.customer_name, txn.amount_paise, txn.transaction_type) == ("Ramesh", 7000, "credit")
    assert state[vw.FLASH][0] == "success"


def test_two_consecutive_commands_are_independent(db, wav_bytes):
    state, stt = {}, FakeStt(RAMESH, SURESH)
    vw.handle_event(state, event("e1", wav_bytes), stt)
    save(state, db, "Ramesh", 70, "credit")
    assert state[vw.ACK_EVENT] == "e1"
    vw.handle_event(state, event("e2", wav_bytes), stt)
    assert vw.pending(state) == ("e2", SURESH)
    save(state, db, "Suresh", 100, "repayment")
    assert state[vw.ACK_EVENT] == "e2"
    rows = {t.customer_name: (t.amount_paise, t.transaction_type) for t in list_transactions(db)}
    assert rows == {"Ramesh": (7000, "credit"), "Suresh": (10000, "repayment")}


def test_reject_saves_nothing_acknowledges_and_next_event_works(db, wav_bytes):
    state, stt = {}, FakeStt(RAMESH, SURESH)
    vw.handle_event(state, event("e1", wav_bytes), stt)
    vw.reject_pending(state)
    assert state[vw.ACK_EVENT] == "e1" and vw.pending(state) is None
    assert list_transactions(db) == []
    assert vw.handle_event(state, event("e2", wav_bytes), stt) == "pending"


def test_replayed_event_is_not_reprocessed(db, wav_bytes):
    """Streamlit returns the same component value on every rerun until the browser sends a new one."""
    state, stt = {}, FakeStt(RAMESH)
    assert vw.handle_event(state, event("e1", wav_bytes), stt) == "pending"
    assert vw.handle_event(state, event("e1", wav_bytes), stt) == "duplicate"
    save(state, db, "Ramesh", 70, "credit")
    assert vw.handle_event(state, event("e1", wav_bytes), stt) == "duplicate"  # even after the save
    assert stt.calls == 1 and len(list_transactions(db)) == 1


class _Interrupted(BaseException):
    """Stands in for Streamlit interrupting a running script (a BaseException)."""


def test_run_interrupted_mid_transcription_reprocesses_instead_of_losing_the_event(wav_bytes):
    state = {}

    def interrupted(*_a, **_k):
        raise _Interrupted

    with pytest.raises(_Interrupted):
        vw.handle_event(state, event("e1", wav_bytes), interrupted)
    assert state.get(vw.LAST_EVENT) is None  # still eligible for processing
    assert vw.handle_event(state, event("e1", wav_bytes), FakeStt(RAMESH)) == "pending"


def test_double_confirm_cannot_create_a_second_transaction(db, wav_bytes):
    state = {}
    vw.handle_event(state, event("e1", wav_bytes), FakeStt(RAMESH))
    save(state, db, "Ramesh", 70, "credit")
    with pytest.raises(LedgerError, match="no transcript"):
        save(state, db, "Ramesh", 70, "credit")
    assert len(list_transactions(db)) == 1


def test_idempotency_key_is_stable_per_event_even_if_state_is_restored(db, wav_bytes):
    state = {}
    vw.handle_event(state, event("e1", wav_bytes), FakeStt(RAMESH))
    snapshot = dict(state)
    save(state, db, "Ramesh", 70, "credit")
    save(dict(snapshot), db, "Ramesh", 70, "credit")  # e.g. a retry after a lost session
    assert len(list_transactions(db)) == 1
    assert balance_paise(db, list_customers(db)[0].id) == 7000
    assert list_transactions(db)[0].id and vw.idempotency_key("e1") == "voice:e1"


def test_failed_save_is_not_acknowledged_and_keeps_pending(db, wav_bytes, monkeypatch):
    state = {}
    vw.handle_event(state, event("e1", wav_bytes), FakeStt(RAMESH))
    with pytest.raises(LedgerError):
        save(state, db, "Ramesh", 0, "credit")  # invalid amount
    assert state.get(vw.ACK_EVENT) is None
    assert vw.pending(state) == ("e1", RAMESH)

    def exploding_add(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(vw, "add_transaction", exploding_add)
    with pytest.raises(RuntimeError):
        save(state, db, "Ramesh", 70, "credit")
    assert state.get(vw.ACK_EVENT) is None and vw.pending(state) is not None  # user can retry


def test_prisma_error_releases_the_browser_queue(wav_bytes):
    state = {}

    def failing(*_a, **_k):
        raise PrismaError("Prisma took too long to respond.")

    assert vw.handle_event(state, event("e1", wav_bytes), failing) == "error"
    assert state[vw.ACK_EVENT] == "e1" and vw.pending(state) is None
    assert state[vw.FLASH] == ("error", "Prisma took too long to respond.")


def test_unexpected_exception_releases_queue_without_leaking_details(wav_bytes):
    state = {}

    def crashing(*_a, **_k):
        raise KeyError("SECRET-API-KEY-123")

    assert vw.handle_event(state, event("e1", wav_bytes), crashing) == "error"
    assert state[vw.ACK_EVENT] == "e1"
    assert "SECRET" not in state[vw.FLASH][1]
    assert vw.handle_event(state, event("e2", wav_bytes), FakeStt(RAMESH)) == "pending"  # progresses


def test_malformed_browser_event_is_acknowledged(wav_bytes):
    state, stt = {}, FakeStt()
    assert vw.handle_event(state, event("e1", b"", error="The browser sent corrupted audio."), stt) == "error"
    assert state[vw.ACK_EVENT] == "e1" and stt.calls == 0


def test_long_segment_is_rejected_before_any_api_call(tmp_path):
    state, stt = {}, FakeStt(RAMESH)
    too_long = make_wav(tmp_path / "long.wav", seconds=31, rate=8000)
    assert vw.handle_event(state, event("e1", too_long), stt) == "error"
    assert stt.calls == 0 and state[vw.ACK_EVENT] == "e1"
    assert "30" in state[vw.FLASH][1]


def test_segment_at_the_limit_is_accepted(tmp_path):
    state = {}
    ok = make_wav(tmp_path / "edge.wav", seconds=29.5, rate=8000)
    assert vw.handle_event(state, event("e1", ok), FakeStt(RAMESH)) == "pending"


def test_invalid_wav_is_rejected_before_any_api_call():
    state, stt = {}, FakeStt(RAMESH)
    assert vw.handle_event(state, event("e1", b"this is not a wav file"), stt) == "error"
    assert stt.calls == 0 and state[vw.ACK_EVENT] == "e1"


def test_new_event_after_stop_start_supersedes_unconfirmed_one_without_saving(db, wav_bytes):
    state, stt = {}, FakeStt(RAMESH, SURESH)
    vw.handle_event(state, event("old-session", wav_bytes), stt)
    vw.handle_event(state, event("new-session", wav_bytes), stt)
    assert vw.pending(state) == ("new-session", SURESH)
    assert list_transactions(db) == []


def test_upload_fallback_confirms_without_acknowledging_the_browser(db):
    state = {}
    vw.start_upload(state, RAMESH, "abc")
    save(state, db, "Ramesh", 70, "credit")
    assert state.get(vw.ACK_EVENT) is None
    assert list_transactions(db)[0].amount_paise == 7000


def test_temp_audio_files_are_removed(wav_bytes, tmp_path, monkeypatch):
    import tempfile
    from pathlib import Path

    seen = []

    def spy(path, **_):
        seen.append(Path(path))
        assert Path(path).is_file()
        return {"transcript": RAMESH}

    vw.handle_event({}, event("e1", wav_bytes), spy)
    assert seen and not seen[0].exists()
