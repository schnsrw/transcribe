#!/usr/bin/env bash
# Generate the audio fixtures used by tests/live/run_cases.py.
# Uses macOS `say` — does not work on Linux/Windows. The .wav files
# are gitignored (see tests/e2e/.gitignore + .gitignore) so each
# developer regenerates them locally.
#
# Voices we need (built into macOS):
#   Samantha  en_US
#   Rishi     en_IN   (Indian English)
#   Lekha     hi_IN
#   Paulina   es_MX
#   Anna      de_DE
#
# Check yours with:  say -v ?

set -euo pipefail
cd "$(dirname "$0")/../e2e/fixtures"

WAVS=(
    'yes:Samantha:Yes.'
    'hello-en:Samantha:The quick brown fox jumps over the lazy dog. This is a test of the transcription system.'
    'pause-en:Samantha:Hello there. [[slnc 1500]] How are you doing today?'
    'numbers-en:Samantha:One. Two. Three. Four. Five. Six. Seven. Eight. Nine. Ten.'
    'indian-en:Rishi:Hello, my name is Rajesh. I work at the office in Bangalore. Today is a good day.'
    'hello-hi:Lekha:नमस्ते, यह एक परीक्षण है।'
    'hindi-long:Lekha:नमस्ते, मेरा नाम राजेश है। मैं बेंगलुरु में रहता हूं। आज मौसम बहुत अच्छा है।'
    'spanish:Paulina:Buenos días, ¿cómo estás hoy? Espero que tengas un buen día.'
    'german:Anna:Guten Tag. Wie geht es Ihnen heute? Ich hoffe, Sie haben einen schönen Tag.'
    'thanks-trap:Samantha:Thank you for watching.'
)

for entry in "${WAVS[@]}"; do
    name=${entry%%:*}
    rest=${entry#*:}
    voice=${rest%%:*}
    text=${rest#*:}
    echo "  -> ${name}.wav  voice=${voice}"
    say -v "$voice" --file-format=WAVE --data-format=LEI16@16000 -o "${name}.wav" "$text"
done

# Long English monologue (used as the "long monologue" case)
say -v Samantha --file-format=WAVE --data-format=LEI16@16000 -o monolog30.wav \
"Hello, I'd like to test how the transcription system handles a much longer continuous monologue. This sentence is just the start. I'm going to keep talking for thirty seconds straight. The whole point is to see whether interim results appear while I speak, or whether the model only emits something after I stop. If everything is wired correctly, I should see interim transcriptions appearing every few seconds while this audio plays, and then a final at the end. Let's count to ten now. One, two, three, four, five, six, seven, eight, nine, ten."

# Pure-silence buffer (3 s of zeros) for the negative case.
python3 - <<'PY'
import wave
with wave.open('silence.wav', 'wb') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(b'\x00\x00' * 16000 * 3)
PY

echo "Done. Files in $(pwd):"
ls -la *.wav
