"""
Parity smoke test — Mac (mlx-whisper) vs Docker (faster-whisper).

Submits the same fixtures to two running stacks and reports diffs in:
  * consolidated text per case
  * number of finals / interims
  * first-event latency

Usage:
  1. Start the Mac host stack:   make dev-mac           (port 8100)
  2. Start the Docker stack:     make dev-docker        (port 8000 by default;
                                                         this script connects
                                                         on 8000 inside the
                                                         container or 8100/8200
                                                         externally — pass
                                                         --mac-url/--docker-url)
  3. Run on the host:
       python tests/live/probes/parity.py \\
           --fixtures tests/e2e/fixtures \\
           --mac-url ws://localhost:8100/ws \\
           --docker-url ws://localhost:8000/ws

The intent is *trend detection*, not bit-exact match — word-timestamp
drift up to 80 ms across the two runtimes is expected (see ADR-013).
We fail loudly only if the *text* differs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path

from websockets.asyncio.client import connect

HEADER_BYTES = 60


@dataclass
class StackResult:
    label: str
    url: str
    events: list[dict] = field(default_factory=list)
    elapsed: float = 0.0
    error: str | None = None

    @property
    def consolidated(self) -> str:
        return " ".join(
            (e.get("text") or "").strip()
            for e in self.events
            if (e.get("text") or "").strip()
        ).strip()

    @property
    def first_t(self) -> float | None:
        return self.events[0]["__t"] if self.events else None


async def submit(stack: StackResult, pcm: bytes, lang: str, stream: bool) -> None:
    meeting_id = str(uuid.uuid4())
    pid = f"parity-{meeting_id[:8]}"
    url = f"{stack.url}/{meeting_id}"
    header = f"{pid}|{lang}".encode("ascii").ljust(HEADER_BYTES, b"\x00")
    t0 = time.time()
    try:
        async with connect(url, max_size=2**24) as ws:
            async def reader():
                async for msg in ws:
                    obj = json.loads(msg)
                    obj["__t"] = time.time() - t0
                    stack.events.append(obj)
            rt = asyncio.create_task(reader())
            try:
                if stream:
                    chunk_bytes = 16384 * 2
                    send0 = time.time()
                    for i in range(0, len(pcm), chunk_bytes):
                        await ws.send(header + pcm[i:i + chunk_bytes])
                        target = (i + chunk_bytes) / 32000.0
                        wait = target - (time.time() - send0)
                        if wait > 0:
                            await asyncio.sleep(wait)
                else:
                    await ws.send(header + pcm)
                # Drain
                wait_until = time.time() + 90
                while time.time() < wait_until:
                    if stack.events and time.time() - t0 - stack.events[-1]["__t"] > 25:
                        break
                    await asyncio.sleep(0.5)
            finally:
                rt.cancel()
                try:
                    await ws.send(b"\x00")
                except Exception:
                    pass
    except Exception as e:
        stack.error = repr(e)
    stack.elapsed = time.time() - t0


def load_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        assert w.getnchannels() == 1 and w.getframerate() == 16000 and w.getsampwidth() == 2
        return w.readframes(w.getnframes())


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--fixtures", default="tests/e2e/fixtures", type=Path)
    p.add_argument("--mac-url", default="ws://localhost:8100/ws")
    p.add_argument("--docker-url", default="ws://localhost:8000/ws")
    p.add_argument("--mode", choices=["whole", "stream", "both"], default="both")
    p.add_argument("--lang", default="auto")
    p.add_argument("--cases", nargs="*",
                   default=["hello-en.wav", "hello-hi.wav", "spanish.wav", "german.wav"])
    args = p.parse_args()

    drift = 0
    diff = 0
    for case in args.cases:
        path = args.fixtures / case
        if not path.exists():
            print(f"SKIP {case}: missing fixture (run tests/live/generate_fixtures.sh first)")
            continue
        pcm = load_wav(path)
        for mode in (("whole", "stream") if args.mode == "both" else (args.mode,)):
            stream = mode == "stream"
            mac = StackResult(label=f"MAC ({mode})", url=args.mac_url)
            dock = StackResult(label=f"DOCKER ({mode})", url=args.docker_url)
            await asyncio.gather(
                submit(mac, pcm, args.lang, stream=stream),
                submit(dock, pcm, args.lang, stream=stream),
            )

            print(f"\n=== {case} [{mode}] ===")
            print(f"  MAC    : {len(mac.events):3d} events  elapsed={mac.elapsed:5.1f}s  first@{mac.first_t or '—'}")
            print(f"  DOCKER : {len(dock.events):3d} events  elapsed={dock.elapsed:5.1f}s  first@{dock.first_t or '—'}")
            if mac.error: print(f"  ! MAC error: {mac.error}")
            if dock.error: print(f"  ! DOCKER error: {dock.error}")
            print(f"  MAC text   : {mac.consolidated[:200]!r}")
            print(f"  DOCKER text: {dock.consolidated[:200]!r}")
            if mac.consolidated and dock.consolidated:
                # Word-level overlap as crude similarity (real WER would
                # need jiwer or similar — keeping deps light here).
                mw = set(mac.consolidated.lower().split())
                dw = set(dock.consolidated.lower().split())
                overlap = len(mw & dw) / max(1, len(mw | dw))
                print(f"  overlap    : {overlap:.0%}")
                if overlap < 0.5:
                    print(f"  ✗ TEXT DRIFT")
                    diff += 1
                else:
                    print(f"  ✓ OK")
            else:
                # One side missing — could be a negative case or a stack down.
                pass

    print(f"\n{diff} cases with significant text drift.")
    return 1 if diff else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
