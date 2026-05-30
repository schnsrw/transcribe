# Live test harness

Drives the running Docker dev stack with real audio fixtures over the
WebSocket API and reports a PASS/FAIL matrix per case × delivery-mode.

## What it tests

`run_cases.py` covers 13 scenarios, each submitted twice (WHOLE-FILE
single-frame + STREAM 1-s-chunks-at-real-time):

| Category | Cases |
|---|---|
| English | short ("Yes."), pangram, long monologue, mid-sentence pause, counting 1–10, Indian accent |
| Other languages | Hindi short, Hindi longer, Spanish, German |
| Negative (must produce no events) | 3 s of silence, "Thank you for watching" trap |
| Multilingual mode | English audio with `lang=auto` |

For each case it validates:

- The expected key words appear in the concatenated event text
  (or that **no** events came out, for negative cases).
- At least one event arrives within the drain budget.

## Run it

1. Bring up the dev stack:
   ```
   docker compose -f compose.dev.yaml up -d
   ```
2. Generate the audio fixtures (macOS only — uses `say`):
   ```
   bash tests/live/generate_fixtures.sh
   ```
   The `.wav` files land in `tests/e2e/fixtures/` and are gitignored.
3. Stage everything into the container:
   ```
   docker exec casual-sst mkdir -p /tmp/fixtures
   for f in tests/e2e/fixtures/*.wav; do
       docker cp "$f" casual-sst:/tmp/fixtures/$(basename "$f")
   done
   docker cp tests/live/run_cases.py casual-sst:/tmp/run_cases.py
   ```
4. Run:
   ```
   docker exec casual-sst python /tmp/run_cases.py
   ```

Expect ~6–8 minutes end-to-end on Mac M4 (`whisper-small`, CPU).

## Quick probes

`probes/compare.py` submits one fixture in both delivery modes back-to-
back — useful when you've changed the pipeline and want to eyeball one
specific input without running the whole suite:

```
docker cp tests/live/probes/compare.py casual-sst:/tmp/
docker exec casual-sst python /tmp/compare.py /tmp/fixtures/hello-en.wav en
```

It prints every event with its receive timestamp + a consolidated
transcript at the end.

## Interpreting the matrix

```
CASE                                    MODE    PASS  EVENTS   FINALS  1st EVT   ELAPSED
EN long monologue (33 s)                STREAM  ✓     45       12      +4.2s     115.3
                                                ^     ^        ^       ^         ^
                                                |     |        |       |         total wall-clock
                                                |     |        |       latency to first event
                                                |     |        finalized utterances
                                                |     total emissions (interims + finals)
                                                pass/fail
```

For real-time UX the latency-to-first-event matters most — under
**5 s** is the target.

## Last-known-good baseline (whisper-small, Mac M4 CPU)

25 / 25 passing. See [docs/DECISIONS.md](../../docs/DECISIONS.md) ADR-009
through ADR-012 for the bugs this harness uncovered.
