import { defineConfig, devices } from '@playwright/test';
import * as path from 'path';

// 2026-10-04 기술일지 화면 수리 회귀(게이트를 통과하는 user_report_fixture, 전용 포트·데이터).
const root = path.resolve(__dirname, '../..');
const port = Number(process.env.SR_TECHLOG_PORT || 18761);
const data = '.agent-runs/techlog-fix/browser-fixture';
process.env.SR_FIXTURE_DIR = path.join(root, data);
export default defineConfig({
  testDir: './specs', testMatch: /techlog-ui\.spec\.ts/, workers: 1, retries: 0, timeout: 60000,
  outputDir: path.join(root, '.agent-runs/techlog-fix/browser-results'),
  reporter: [['list']],
  use: { baseURL: `http://127.0.0.1:${port}`, locale: 'ko-KR', timezoneId: 'Asia/Seoul', trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  webServer: { command: `.venv/bin/python scripts/dev/user_report_fixture.py --data-dir ${data} --port ${port}`,
    cwd: root, url: `http://127.0.0.1:${port}/health`, reuseExistingServer: false, timeout: 60000 },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }, { name: 'firefox', use: { ...devices['Desktop Firefox'] } }],
});
