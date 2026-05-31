"""
WHOLE-FILE vs STREAM-1s comparison probe.

Submits one WAV in two delivery modes against the live dev server,
prints each event with its receive timestamp, and summarises:

  - first-event latency
  - finals vs interims
  - consolidated transcription

Designed to be ``docker exec``-d inside the running dev container so it
talks to ``ws://localhost:8000/ws`` directly. From the host:

    docker cp tests/live/probes/compare.py casual-sst:/tmp/
    docker cp tests/live/fixtures/hello-en.wav casual-sst:/tmp/
    docker exec casual-sst python /tmp/compare.py /tmp/hello-en.wav en
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
import wave

from websockets.asyncio.client import connect

import os

HEADER_BYTES = 60
CHUNK_BYTES = 16384 * 2          # ≈1.024 s at 16 kHz s16le mono
# Default 8000 matches the in-container view; override with WS_URL=...
# when running on the host against `make dev-mac` (port 8100).
WS_URL = os.environ.get("WS_URL", "ws://localhost:8000/ws")


async def run(label: str, wav_path: str, lang: str, *, stream: bool) -> None:
    meeting_id = str(uuid.uuid4())
    pid = f"probe-{meeting_id[:8]}"
    url = f"{WS_URL}/{meeting_id}"

    with wave.open(wav_path, "rb") as wav:
        assert wav.getnchannels() == 1 and wav.getframerate() == 16000 \
            and wav.getsampwidth() == 2, (
            f"{wav_path}: expected 16 kHz mono s16le"
        )
        pcm = wav.readframes(wav.getnframes())

    audio_secs = len(pcm) / 32000
    print(f"\n========== {label} ==========", flush=True)
    print(f"audio: {audio_secs:.2f}s ({len(pcm)} bytes)  stream={stream}", flush=True)

    t0 = time.time()
    events: list[dict] = []
    async with connect(url, max_size=2**24) as ws:
        header = f"{pid}|{lang}".encode("ascii").ljust(HEADER_BYTES, b"\x00")

        async def reader():
            async for msg in ws:
                obj = json.loads(msg)
                obj["__t"] = time.time() - t0
                events.append(obj)
                kind = obj.get("type", "?").upper()
                txt = obj.get("text", "")
                if len(txt) > 200:
                    txt = txt[:200] + "…"
                print(f"  +{obj['__t']:6.2f}s [{kind:8s}] var={obj.get('variance',0):.2f}  {txt!r}", flush=True)
        rt = asyncio.create_task(reader())

        try:
            if stream:
                send0 = time.time()
                for i in range(0, len(pcm), CHUNK_BYTES):
                    await ws.send(header + pcm[i:i + CHUNK_BYTES])
                    target = (i + CHUNK_BYTES) / 32000.0
                    wait = target - (time.time() - send0)
                    if wait > 0:
                        await asyncio.sleep(wait)
                print(f"  >>> finished streaming at +{time.time()-t0:.2f}s", flush=True)
            else:
                await ws.send(header + pcm)
                print(f"  >>> single frame sent at +{time.time()-t0:.2f}s", flush=True)

            # Drain: stop once 30 s pass without a new event, or 240 s total.
            wait_until = time.time() + 240
            while time.time() < wait_until:
                if events and time.time() - t0 - events[-1]["__t"] > 30:
                    break
                await asyncio.sleep(1)
        finally:
            rt.cancel()
            try:
                await ws.send(b"\x00")
            except Exception:
                pass

    if events:
        finals = [e for e in events if e["type"] == "final"]
        interims = [e for e in events if e["type"] == "interim"]
        first_t = events[0]["__t"]
        last_t = events[-1]["__t"]
        full = " ".join(e["text"] for e in events if e["text"]).strip()
        print(
            f"\n  >>> {len(events)} events "
            f"({len(finals)} final / {len(interims)} interim)\n"
            f"  >>> first @ +{first_t:.2f}s   last @ +{last_t:.2f}s\n"
            f"  >>> consolidated:\n      {full[:600]}",
            flush=True,
        )


async def main() -> None:
    if len(sys.argv) < 3:
        print("usage: compare.py <wav> <lang>", file=sys.stderr)
        sys.exit(2)
    wav, lang = sys.argv[1], sys.argv[2]
    await run("WHOLE-FILE", wav, lang, stream=False)
    await run("STREAM-1s-paced", wav, lang, stream=True)


if __name__ == "__main__":
    asyncio.run(main())
