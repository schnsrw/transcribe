import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright E2E config for Casual-SST.
 *
 * Drives a Chromium with fake-media flags so the demo's `getUserMedia`
 * call returns a deterministic audio source. The audio file path is set
 * via env var so CI can mount a fixture without checking it in.
 *
 *   FAKE_AUDIO=/abs/path/to/16k-mono.wav npx playwright test
 *
 * The dev stack (compose.dev.yaml) must already be running:
 *   docker compose -f ../../compose.dev.yaml up -d
 *   # Demo at http://localhost:8080, API at ws://localhost:8000/ws
 */

const FAKE_AUDIO = process.env.FAKE_AUDIO ?? '';

export default defineConfig({
  testDir: '.',
  fullyParallel: false,           // server is single-instance
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { outputFolder: 'playwright-report', open: 'never' }]],
  timeout: 60_000,
  use: {
    baseURL: process.env.DEMO_URL ?? 'http://localhost:8180',
    trace: 'on-first-retry',
    video: 'retain-on-failure',
    screenshot: 'only-on-failure',
    // Grant microphone permission automatically.
    permissions: ['microphone'],
    launchOptions: {
      args: [
        '--autoplay-policy=no-user-gesture-required',
        '--use-fake-ui-for-media-stream',
        ...(FAKE_AUDIO ? [
          '--use-fake-device-for-media-stream',
          `--use-file-for-fake-audio-capture=${FAKE_AUDIO}`,
        ] : []),
      ],
    },
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
