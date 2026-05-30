"""
Effectiveness test harness for Casual-SST.

Drives the live WebSocket server from inside the same container with a
matrix of audio fixtures × delivery modes (STREAM = real-time paced
1 s chunks, WHOLE = single frame). For each case it:

  1. Reads the WAV fixture
  2. Opens the WS, sends the audio per the chosen mode
  3. Collects every server event with its receive timestamp
  4. Scores the result against the case's expectations
  5. Prints a one-line PASS/FAIL summary, then a final report table

Run inside the casual-sst container (so it talks to localhost:8000):

    docker exec casual-sst python /app/tests/live/run_cases.py

Mount the fixtures + this script via the compose.dev.yaml ``src``
volume bind, or ``docker cp`` them before running.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
import wave
from dataclasses import dataclass, field
from typing import Callable

from websockets.asyncio.client import connect

HEADER_BYTES = 60
CHUNK_BYTES = 16384 * 2          # ≈1.024 s at 16 kHz s16le mono
WS_URL = "ws://localhost:8000/ws"
# Drain time budgets — Whisper-small on amd64 emulation can take 20-30 s
# to produce the first event for short non-English clips streamed in
# 1 s chunks. Be patient; the GLOBAL_MAX still caps the total.
DRAIN_AFTER_LAST_EVENT_S = 25.0  # stop once N s have passed since last event
DRAIN_IF_NO_EVENTS_S = 60.0      # if events list is empty, stop after this
GLOBAL_MAX = 240.0               # hard cap per case


# ---------------------------------------------------------------------------
# Case definition
# ---------------------------------------------------------------------------
@dataclass
class Case:
    name: str
    wav: str
    lang: str
    # Expectation predicates — each takes the concatenated text and event list.
    # Use a list so a case can assert multiple things.
    expect: list[tuple[str, Callable[[str, list[dict]], bool]]]
    # Skip the streaming mode for this case (e.g. for very short clips).
    skip_stream: bool = False
    # Skip the whole-file mode (rare).
    skip_whole: bool = False


def contains_all(*needles: str) -> Callable[[str, list[dict]], bool]:
    def check(text: str, _events) -> bool:
        low = text.lower()
        return all(n.lower() in low for n in needles)
    return check


def no_events_or_empty() -> Callable[[str, list[dict]], bool]:
    def check(text: str, events) -> bool:
        return len(events) == 0 or all(not (e.get("text") or "").strip() for e in events)
    return check


def at_least_n_finals(n: int) -> Callable[[str, list[dict]], bool]:
    def check(_text, events) -> bool:
        return sum(1 for e in events if e.get("type") == "final") >= n
    return check


def at_least_n_events(n: int) -> Callable[[str, list[dict]], bool]:
    def check(_text, events) -> bool:
        return len(events) >= n
    return check


# ---------------------------------------------------------------------------
# WS submission
# ---------------------------------------------------------------------------
async def submit(wav_path: str, lang: str, *, mode: str) -> tuple[list[dict], float]:
    """Send ``wav_path`` via WS and return (events, elapsed_seconds).

    ``mode`` is ``"stream"`` (1 s chunks at real-time) or ``"whole"`` (one
    binary frame containing the entire PCM payload).
    """
    meeting_id = str(uuid.uuid4())
    pid = f"probe-{meeting_id[:8]}"
    url = f"{WS_URL}/{meeting_id}"

    with wave.open(wav_path, "rb") as wav:
        assert wav.getnchannels() == 1 and wav.getframerate() == 16000 and wav.getsampwidth() == 2, (
            f"{wav_path}: expected 16 kHz mono s16le"
        )
        pcm = wav.readframes(wav.getnframes())

    header = f"{pid}|{lang}".encode("ascii").ljust(HEADER_BYTES, b"\x00")

    events: list[dict] = []
    t0 = time.time()
    async with connect(url, max_size=2**24) as ws:
        async def reader():
            async for msg in ws:
                obj = json.loads(msg)
                obj["__t"] = time.time() - t0
                events.append(obj)
        reader_task = asyncio.create_task(reader())

        try:
            if mode == "stream":
                send0 = time.time()
                for i in range(0, len(pcm), CHUNK_BYTES):
                    await ws.send(header + pcm[i:i + CHUNK_BYTES])
                    target = (i + CHUNK_BYTES) / 32000.0
                    wait = target - (time.time() - send0)
                    if wait > 0:
                        await asyncio.sleep(wait)
            else:
                await ws.send(header + pcm)

            # Drain: bail when we've had ``DRAIN_AFTER_LAST_EVENT_S`` of
            # quiet AFTER at least one event, or ``DRAIN_IF_NO_EVENTS_S``
            # of total wall-clock with no events at all (negative cases).
            send_done = time.time()
            while time.time() - t0 < GLOBAL_MAX:
                now_dt = time.time() - t0
                if events:
                    quiet = now_dt - events[-1]["__t"]
                    if quiet > DRAIN_AFTER_LAST_EVENT_S:
                        break
                else:
                    if time.time() - send_done > DRAIN_IF_NO_EVENTS_S:
                        break
                await asyncio.sleep(0.5)
        finally:
            reader_task.cancel()
            try:
                await ws.send(b"\x00")
            except Exception:
                pass

    return events, time.time() - t0


# ---------------------------------------------------------------------------
# Case definitions
# ---------------------------------------------------------------------------
FIXTURES = "/tmp/fixtures"

CASES: list[Case] = [
    Case(
        name="EN short utterance",
        wav=f"{FIXTURES}/yes.wav",
        lang="en",
        expect=[("contains 'yes'", contains_all("yes")),
                ("at least 1 event", at_least_n_events(1))],
    ),
    Case(
        name="EN pangram (5 s)",
        wav=f"{FIXTURES}/hello-en.wav",
        lang="en",
        expect=[("contains 'quick brown fox'", contains_all("quick brown fox")),
                ("contains 'lazy dog'", contains_all("lazy dog"))],
    ),
    Case(
        name="EN long monologue (33 s)",
        wav=f"{FIXTURES}/monolog30.wav",
        lang="en",
        # WHOLE-FILE emits 1-2 finals + 1 big interim (entire transcript
        # in the interim payload); STREAM emits many more finals. Both
        # paths are valid — only require the text content + at least one
        # final, not a fixed final count.
        expect=[("contains 'monologue'", contains_all("monologue")),
                ("contains 'after I stop'", contains_all("after i stop")),
                ("at least 1 final", at_least_n_finals(1))],
    ),
    Case(
        name="EN mid-sentence pause",
        wav=f"{FIXTURES}/pause-en.wav",
        lang="en",
        expect=[("contains 'hello'", contains_all("hello")),
                ("contains 'how are you'", contains_all("how are you"))],
    ),
    Case(
        name="EN counting 1..10",
        wav=f"{FIXTURES}/numbers-en.wav",
        lang="en",
        expect=[("contains '1'", contains_all("1")),
                ("contains '10'", contains_all("10"))],
    ),
    Case(
        name="EN with Indian accent (Rishi)",
        wav=f"{FIXTURES}/indian-en.wav",
        lang="en",
        expect=[("contains 'bangalore'", contains_all("bangalore")),
                ("contains 'rajesh'", contains_all("rajesh"))],
    ),
    Case(
        name="Hindi short",
        wav=f"{FIXTURES}/hello-hi.wav",
        lang="hi",
        expect=[("contains 'नमस्ते'", contains_all("नमस्ते")),
                ("at least 1 event", at_least_n_events(1))],
    ),
    Case(
        name="Hindi longer",
        wav=f"{FIXTURES}/hindi-long.wav",
        lang="hi",
        expect=[("contains 'राजेश' or 'बेंगलुरु'", lambda t, _e: "राजेश" in t or "बेंगलुरु" in t),
                ("at least 1 event", at_least_n_events(1))],
    ),
    Case(
        name="Spanish",
        wav=f"{FIXTURES}/spanish.wav",
        lang="es",
        expect=[("contains 'buenos'", contains_all("buenos")),
                ("at least 1 event", at_least_n_events(1))],
    ),
    Case(
        name="German",
        wav=f"{FIXTURES}/german.wav",
        lang="de",
        expect=[("contains 'guten tag'", contains_all("guten tag")),
                ("at least 1 event", at_least_n_events(1))],
    ),
    Case(
        name="Silence (3 s) — should emit NOTHING",
        wav=f"{FIXTURES}/silence.wav",
        lang="en",
        expect=[("no transcription events", no_events_or_empty())],
    ),
    Case(
        name="Hallucination trap ('Thank you for watching') — should DROP",
        wav=f"{FIXTURES}/thanks-trap.wav",
        lang="en",
        expect=[("no transcription events", no_events_or_empty())],
    ),
    Case(
        name="EN whole file auto-detect",
        wav=f"{FIXTURES}/hello-en.wav",
        lang="auto",
        expect=[("contains 'quick brown fox'", contains_all("quick brown fox"))],
        skip_stream=True,  # auto-detect routing == whole-file path on local config
    ),
]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
@dataclass
class ModeResult:
    mode: str
    events: list[dict] = field(default_factory=list)
    elapsed: float = 0.0
    checks: list[tuple[str, bool]] = field(default_factory=list)

    @property
    def consolidated(self) -> str:
        return " ".join((e.get("text") or "").strip() for e in self.events if (e.get("text") or "").strip())

    @property
    def first_event_t(self) -> float | None:
        return self.events[0]["__t"] if self.events else None

    @property
    def num_finals(self) -> int:
        return sum(1 for e in self.events if e.get("type") == "final")

    @property
    def num_interims(self) -> int:
        return sum(1 for e in self.events if e.get("type") == "interim")

    @property
    def pass_(self) -> bool:
        return all(ok for _, ok in self.checks)


@dataclass
class CaseResult:
    case: Case
    whole: ModeResult | None = None
    stream: ModeResult | None = None


async def run_case(case: Case) -> CaseResult:
    print(f"\n========== {case.name} ==========", flush=True)
    cr = CaseResult(case=case)

    for mode in ("whole", "stream"):
        if mode == "whole" and case.skip_whole: continue
        if mode == "stream" and case.skip_stream: continue
        print(f"  [{mode.upper()}] ...", flush=True)
        try:
            events, elapsed = await submit(case.wav, case.lang, mode=mode)
        except Exception as e:
            print(f"    ERROR: {e}", flush=True)
            mr = ModeResult(mode=mode, elapsed=0.0)
            mr.checks = [("submit failed", False)]
        else:
            mr = ModeResult(mode=mode, events=events, elapsed=elapsed)
            text = mr.consolidated
            mr.checks = [(label, predicate(text, events)) for label, predicate in case.expect]
            first = f"{mr.first_event_t:.1f}s" if mr.first_event_t is not None else "—"
            print(f"    elapsed={elapsed:.1f}s  events={len(events)} (final={mr.num_finals}, interim={mr.num_interims})  first@{first}", flush=True)
            print(f"    text: {text[:200]}{'…' if len(text) > 200 else ''}", flush=True)
            for label, ok in mr.checks:
                print(f"      {'✓' if ok else '✗'} {label}", flush=True)
        setattr(cr, mode, mr)
    return cr


def print_summary(results: list[CaseResult]) -> None:
    print("\n" + "=" * 110, flush=True)
    print(f"{'CASE':<46} {'MODE':<7} {'PASS':<5} {'EVENTS':<8} {'FINALS':<7} {'1st EVT':<9} {'ELAPSED':<8}", flush=True)
    print("-" * 110, flush=True)
    total = 0
    passed = 0
    for cr in results:
        for mr in (cr.whole, cr.stream):
            if mr is None: continue
            total += 1
            if mr.pass_: passed += 1
            first = f"+{mr.first_event_t:.1f}s" if mr.first_event_t is not None else "—"
            print(f"{cr.case.name[:46]:<46} {mr.mode.upper():<7} {'✓' if mr.pass_ else '✗':<5} {len(mr.events):<8} {mr.num_finals:<7} {first:<9} {mr.elapsed:<8.1f}", flush=True)
    print("-" * 110, flush=True)
    print(f"PASSED: {passed} / {total}", flush=True)


async def main() -> int:
    results: list[CaseResult] = []
    for case in CASES:
        results.append(await run_case(case))
    print_summary(results)
    return 0 if all(
        all(mr.pass_ for mr in (cr.whole, cr.stream) if mr is not None)
        for cr in results
    ) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
