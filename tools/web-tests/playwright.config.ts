import { defineConfig, devices } from '@playwright/test';
import * as path from 'path';
import * as os from 'os';

// fixture 서버: 운영 data/ 와 분리된 임시 루트 + 외부 부작용 차단(SAFETYREPORT_FIXTURE_MODE)
const repoRoot = path.resolve(__dirname, '..', '..');
const port = Number(process.env.SR_TEST_PORT ?? 18649);
const python = process.env.SR_PYTHON
  ?? path.join(repoRoot, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const dataDir = process.env.SR_FIXTURE_DIR ?? path.join(os.tmpdir(), `safetyreport-webtests-${port}`);

export const baseURL = `http://127.0.0.1:${port}`;
export const fixtureDataDir = dataDir;

export default defineConfig({
  testDir: './specs',
  outputDir: './test-results',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: './playwright-report' }]],
  use: {
    baseURL,
    locale: 'ko-KR',
    timezoneId: 'Asia/Seoul',
    viewport: { width: 1440, height: 900 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: `"${python}" scripts/dev/fixture_server.py serve --data-dir "${dataDir}" --port ${port} --reset`,
    cwd: repoRoot,
    url: `${baseURL}/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    stdout: 'ignore',
    stderr: 'pipe',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
    { name: 'firefox', use: { ...devices['Desktop Firefox'], viewport: { width: 1440, height: 900 } } },
    { name: 'webkit', use: { ...devices['Desktop Safari'], viewport: { width: 1440, height: 900 } } },
  ],
});
