# Hallucination guards — the Whisper-turbo stack

`whisper-large-v3-turbo` is fast (6× over v3 at single-GPU INT8) but
hallucinates more readily on noisy / silent / very-short audio. This
document is the inventory of guards we layer on top of it. Removing any
one is acceptable; removing several at once will reintroduce the failure
mode that prompted this repo.

The guards stack — each one rejects a slightly different failure case.
Numbers are defaults; all are tunable via `config/*.yaml`.

## Pre-decode

| Guard | Default | Owned by | Drops |
|---|---|---|---|
| Silero VAD gate before any transcribe call | threshold 0.5 | `vad.is_silent` + `participant.push` | Pure-silence buffers, music-only clips |
| Minimum *detected speech* per finalize | `min_speech_ms = 250` | `participant.force_short_flush` | Coughs, lip smacks, single phonemes |

## At-decode (faster-whisper arguments)

These are the Whisper knobs we either turn up or lock down. All flow
through `backends/whisper_turbo.py`.

| Argument | Whisper default | Our default | Why |
|---|---|---|---|
| `no_speech_threshold` | 0.6 | **0.85** | Turbo is more confident it heard speech in silent clips than v3. Raise the bar. |
| `log_prob_threshold` | -1.0 | **-0.4** | Drops low-confidence segments that are usually hallucinations. |
| `compression_ratio_threshold` | 2.4 | **2.0** | A higher compression ratio means repeated tokens — turbo loops "you you you you" on near-silence. |
| `hallucination_silence_threshold` | None | **2.0** | faster-whisper ≥ 1.0 feature; skips silent regions within a buffer instead of inventing text over them. |
| `condition_on_previous_text` | True | **False** (hard-coded) | The largest single hallucination cause. Whisper's own prior text snowballs errors. |
| `beam_size` | 5 | **1** | Counter-intuitive but true: higher beam *increases* hallucinations on noisy audio because more search budget finds more confabulations. |
| `initial_prompt` | None | per-participant profile prompt | Replaces conditioning-on-prev-text with a controlled, deny-listed seed. |

`condition_on_previous_text` is asserted False in code regardless of
what config says — see `backends/whisper_turbo.py` and ADR-003.

## Post-decode

| Guard | Default | Owned by | Drops |
|---|---|---|---|
| Cut-mark requires avg word prob ≥ 0.7 | `cut_mark.min_probability` | `cut_mark.find` | Low-confidence "finals". Stay as interim until they earn the upgrade. |
| Force biggest-gap split when audio > 8 s | `force_split_after_s: 8` (turbo-specific override) | `cut_mark.find` | Runaway over-long transcripts that snowball. Skynet used 10 s; turbo loops sooner. |
| Hallucination deny-list (per-lang + `*`) | `config.hallucination.denylist` | `filters.is_hallucination` | Training-corpus artifacts: "thank you for watching", "subtitles by", "amara.org", "♪", bare "you", etc. |
| Minimum phrase probability after decode | `min_phrase_prob: 0.6` | `filters.is_hallucination` | Decoded but low-confidence — likely confabulation. |
| Repetition detector (same token ≥ 4 times) | hard-coded | `filters._repeats` | Catches loops the compression-ratio threshold missed. |
| `initial_prompt` blacklist (skip seeding bad finals) | `['. .', '...']` plus `min_prob_for_seeding: 0.7` | `participant._record_final` | Prevents a noisy final from poisoning the next call's prompt. |

## Why the layering matters

No single guard catches every hallucination class:

- VAD alone misses the case where there's faint background speech but
  no real signal (TV in the room).
- `no_speech_threshold` alone misses the loop case ("you you you").
- The deny-list alone misses novel hallucinations.
- The repetition detector alone misses single-shot fabrications.

Each guard is cheap (microseconds). The combined drop rate of bad
transcriptions is much higher than any single guard.

## Tuning checklist

If you see hallucinations leaking through:

1. **Find the failing clip.** Save the PCM (use
   `WHISPER_RETURN_TRANSCRIBED_AUDIO=true` for one call).
2. **Decide which guard *should* have caught it.** If it's silence,
   bump `no_speech_threshold`. If it's a loop,
   tighten `compression_ratio_threshold`. If it's a known phrase,
   add it to the deny-list.
3. **Re-run the integration test** that asserts the clip is rejected.
4. **Document the tune** in the relevant ADR comment thread.

If you tighten thresholds too far, the symptom is "model returns nothing
for borderline-but-valid speech". Watch the `final` count metric.

## What we deliberately do NOT do

- Run Whisper twice and majority-vote — too slow, doesn't help on
  silent-input hallucinations (both runs agree on the wrong text).
- Train a binary "is_hallucination" classifier on output text —
  brittle; the deny-list is good enough and is data not code.
- Use Whisper's own `language=None` detection per-chunk on `auto` —
  flaps badly; LID worker is more controlled.
