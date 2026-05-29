# Wire protocol

The WebSocket protocol is byte-compatible with Skynet's `streaming_whisper`
module. Jigasi can point at Casual-SST with no config change.

## Connect

```
ws://HOST:PORT/ws/{MEETING_ID}?auth_token=<short-lived JWT>
```

- `MEETING_ID` is opaque, treat as a string.
- `auth_token` is omitted when `server.bypass_auth: true` (the default in
  `config/local.yaml`).

## Client → Server binary frame

```
┌────────────────────────────┬─────────────────────────────┐
│        Header (60B)        │      PCM payload (rest)     │
└────────────────────────────┴─────────────────────────────┘

Header  := participant_id "|" language    (ASCII, null-padded to 60 bytes)
PCM     := raw 16 kHz mono s16le, no WAV header
```

Recommended payload size: ~32 KB (≈ 1 second of audio). The server
accepts any size ≥ 60 bytes + 2 bytes of PCM.

### `language` field values

| Value | Meaning | Routes to |
|---|---|---|
| `en`, `hi`, `zh`, `ar`, `fr`, `de`, `es`, `it`, `ja`, `ko`, `nl`, `pt`, `ru` | Single locked language | Voxtral (per config) |
| `ta`, `bn`, `mr`, `te`, `pa`, `gu`, `kn`, `ml` | Indic locked language | IndicConformer |
| `hi-en`, `ta-en`, `bn-en`, `hinglish` | Code-switched, multilingual mode | IndicConformer (code-switch enabled) |
| `auto`, `multi` | Multilingual mode, no language hint | Voxtral with `language=None` |
| anything else | Fallback | `*` route (Whisper-turbo by default) |

### Disconnect frame

A single byte `0x00` signals graceful disconnect.

## Server → Client JSON messages

Each transcription chunk is a JSON object:

```json
{
  "id":             "01904a4f-...-...-...-...",
  "participant_id": "alice@meet.example",
  "ts":             1730000000123,
  "text":           "hello world",
  "audio":          "",
  "type":           "interim" | "final" | "language_change",
  "variance":       0.92,
  "language":       "en"
}
```

| Field | Type | Meaning |
|---|---|---|
| `id` | string (UUID-v7) | Stable within an utterance; rotates on each `final`. |
| `participant_id` | string | Echoed from the request header. |
| `ts` | int (ms since epoch) | Start timestamp of this utterance. |
| `text` | string | Transcription text. Empty for `language_change`. |
| `audio` | string | Base64 WAV of the finalized audio; empty unless `WHISPER_RETURN_TRANSCRIBED_AUDIO=true`. |
| `type` | enum | See below. |
| `variance` | float [0..1] | Average per-word probability. |
| `language` | string | Detected/active language code. |

### Message types

- `interim` — non-final transcription, may be revised.
- `final` — finalized; the next `interim` starts a fresh utterance.
- `language_change` — emitted once when `LangState` switches the active
  backend. Useful for the client to re-style the next utterance.

### `language_change` event

```json
{
  "id": "...",
  "participant_id": "alice@meet.example",
  "ts": 1730000000456,
  "text": "",
  "type": "language_change",
  "variance": 0.0,
  "language": "ta"
}
```

`language` is the **new** active language. Clients that ignore unknown
`type` values (the recommended posture) will simply not see these.

## Backwards compatibility guarantees

- The 60-byte header layout will not change.
- Existing fields in the server→client JSON will not be removed or
  renamed.
- New optional fields and new `type` enum values may be added.

## Examples

### JavaScript (browser)

```js
const ws = new WebSocket(
  `ws://localhost:8000/ws/${meetingId}` +
  (jwt ? `?auth_token=${encodeURIComponent(jwt)}` : '')
);
ws.binaryType = 'arraybuffer';

function buildFrame(participantId, lang, pcmInt16) {
  const header = new Uint8Array(60);
  const text = new TextEncoder().encode(`${participantId}|${lang}`);
  header.set(text.subarray(0, 60));
  const frame = new Uint8Array(60 + pcmInt16.byteLength);
  frame.set(header, 0);
  frame.set(new Uint8Array(pcmInt16.buffer), 60);
  return frame;
}
```

### Python (test client)

```python
import struct

def build_frame(participant_id: str, lang: str, pcm: bytes) -> bytes:
    header = f"{participant_id}|{lang}".encode("ascii")
    header = header.ljust(60, b"\x00")
    return header + pcm
```
