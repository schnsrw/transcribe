/**
 * Casual-SST end-to-end smoke tests.
 *
 * The intent here is plumbing, not transcription accuracy:
 *
 *   1. Demo HTML loads, UI is wired up correctly.
 *   2. Clicking *Transcribe* opens a WebSocket to the API.
 *   3. Binary frames are being sent at the expected cadence
 *      (≈ 1 frame/sec at 16 kHz × 16384-sample buffers).
 *   4. *If* a fake audio WAV is provided via the FAKE_AUDIO env var,
 *      we also wait for the server to emit at least one final result.
 *
 * Accuracy testing belongs in the Python integration suite (real
 * audio + real Whisper) — the Playwright test guards the browser side
 * only.
 */

import { test, expect, Page } from '@playwright/test';

const hasFakeAudio = !!process.env.FAKE_AUDIO;

async function waitForWsConnected(page: Page) {
  await page.waitForFunction(() => {
    const dot = document.getElementById('ws-status');
    return dot?.classList.contains('connected');
  }, undefined, { timeout: 15_000 });
}


test.describe('demo UI', () => {
  test('loads with expected controls', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.brand .name')).toHaveText('Casual-SST');
    await expect(page.locator('#btn-start')).toBeEnabled();
    await expect(page.locator('#btn-stop')).toBeDisabled();
  });

  test('lang dropdown includes core + virtual codes', async ({ page }) => {
    await page.goto('/');
    const options = await page.locator('#lang-select option').allTextContents();
    const codes = options.map(o => o.split('—').pop()?.trim());
    for (const expected of ['en', 'hi', 'ta', 'hinglish', 'hi-en', 'auto']) {
      expect(codes).toContain(expected);
    }
  });
});


test.describe('transcription flow', () => {
  test('connects WS and starts sending binary frames', async ({ page }) => {
    // Capture sent WS payloads at the protocol level using the CDP.
    let binaryFramesSent = 0;
    page.on('websocket', ws => {
      ws.on('framesent', frame => {
        if (frame.payload instanceof Buffer) binaryFramesSent += 1;
      });
    });

    await page.goto('/');
    await page.locator('#btn-start').click();
    await waitForWsConnected(page);

    // Give it ~2.5 s — the AudioWorklet flushes every ~1 s.
    await page.waitForTimeout(2_500);
    expect(binaryFramesSent).toBeGreaterThanOrEqual(1);
  });

  test.skip(!hasFakeAudio, 'requires FAKE_AUDIO env var pointing at a 16kHz mono WAV');
  test('receives at least one final from the server', async ({ page }) => {
    await page.goto('/');
    await page.locator('#btn-start').click();
    await waitForWsConnected(page);

    // Wait up to 30 s for a final row to render.
    await expect(page.locator('.final-row').first()).toBeVisible({ timeout: 30_000 });

    const text = await page.locator('.final-row .text').first().innerText();
    expect(text.trim().length).toBeGreaterThan(0);
  });
});
