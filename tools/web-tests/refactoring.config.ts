import { defineConfig, devices } from '@playwright/test';
import * as path from 'path';

const root = path.resolve(__dirname, '../..');
const port = Number(process.env.SR_TEST_PORT || 18743);
const data = '.agent-runs/refactoring-implementation/browser-data';
process.env.SR_FIXTURE_DIR = path.join(root, data);
export default defineConfig({
  testDir: './specs',
  testMatch: /(?:refactoring-contracts|settings-pages|duplicates-manage|list-interactions|stats-renewal|stats-top|stats-layout|stats-drilldown)\.spec\.ts/,
  outputDir: path.join(root, '.agent-runs/refactoring-implementation/browser-results'),
  workers: 1, retries: 0, timeout: 45000,
  reporter: [['list'], ['json', { outputFile: path.join(root, '.agent-runs/refactoring-implementation/browser-results.json') }]],
  use: { baseURL: `http://127.0.0.1:${port}`, locale: 'ko-KR', timezoneId: 'Asia/Seoul', trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  webServer: { command: `.venv/bin/python scripts/dev/user_report_fixture.py --data-dir ${data} --port ${port}`,
    cwd: root, url: `http://127.0.0.1:${port}/health`, reuseExistingServer: false, timeout: 60000 },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }, { name: 'firefox', use: { ...devices['Desktop Firefox'] } }],
});
