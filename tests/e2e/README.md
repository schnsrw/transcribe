# E2E tests — Casual-SST

Browser-driven tests that exercise the demo UI against a running
Casual-SST stack. The Python unit + integration suites cover protocol
parsing and pipeline logic; these tests cover that *the browser side
talks to the server side at all*.

## Prerequisites

1. Stack up via Docker:

    ```bash
    docker compose -f ../../compose.dev.yaml up -d
    ```

2. Install the Node deps (Playwright runs on the host, not in a
   container — it drives Chromium):

    ```bash
    npm install
    npm run install:browsers
    ```

## Run

```bash
npm test                                  # headless
npm run test:headed                       # watch the browser
npm run test:debug                        # Playwright inspector
```

By default we cover only the plumbing (UI loads, WS connects, binary
frames are sent). The "actual transcription" test is skipped unless
you provide a fixture:

```bash
FAKE_AUDIO=$PWD/fixtures/hello-16k.wav npm test
```

The WAV must be **16 kHz mono PCM**. Anything else and Chrome's fake
audio device will resample badly and the test will be flaky.

A reasonable place to grab a sample from is LibriSpeech mini:
<https://www.openslr.org/12/>. Place the file in `fixtures/` (gitignored).

## What the tests check

| Test | What it asserts |
|---|---|
| `demo UI > loads with expected controls` | DOM structure + buttons wired correctly |
| `demo UI > lang dropdown includes core + virtual codes` | en, hi, ta, hinglish, hi-en, auto all present |
| `transcription flow > connects WS and starts sending binary frames` | WS opens, ≥1 binary frame leaves the browser within 2.5 s |
| `transcription flow > receives at least one final` | (Requires FAKE_AUDIO) Server emits a `final` event within 30 s |
