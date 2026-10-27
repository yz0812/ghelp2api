import { defineConfig } from '@playwright/test'
import { fileURLToPath } from 'node:url'
const root = fileURLToPath(new URL('../', import.meta.url))
export default defineConfig({
  testDir: './e2e', workers: 1, retries: 0,
  use: { baseURL: 'http://127.0.0.1:18765', browserName: 'chromium',
    channel: process.env.PLAYWRIGHT_CHROME_CHANNEL || undefined,
    screenshot: 'only-on-failure', trace: 'retain-on-failure' },
  webServer: {
    command: `"${process.env.GHELPER_PYTHON || 'python'}" -m uvicorn tests.e2e_server:app --host 127.0.0.1 --port 18765 --no-access-log`,
    cwd: root, url: 'http://127.0.0.1:18765/healthz', reuseExistingServer: !process.env.CI, timeout: 30000
  }
})
