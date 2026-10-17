import { defineConfig, devices } from '@playwright/test';
import * as path from 'path';

// 상세 원본 보기(2026-10-17 명세 4절) 전용 브라우저 스모크. db-optimize-bar.config.ts 와 같이
// user_report_fixture.py(프로세스 안 가짜 중앙 인증)로 게이트를 실제로 통과한 화면에서 검사한다.
// fixture 시드 90000001(SPP-2512-9000001)은 원본 있음, 90000002(SPP-2601-9000002)는 없음.
const root = path.resolve(__dirname, '../..');
const data = '.agent-runs/fixture/raw-detail';
const port = Number(process.env.SR_TEST_PORT || 18962);
const python = process.env.SR_PYTHON ?? '/home/better0101/projects/safetyreport/.agent-runs/venv313/bin/python';

export default defineConfig({
  testDir: './specs',
  testMatch: '**/raw-detail.spec.ts',
  workers: 1,
  retries: 0,
  timeout: 60000,
  outputDir: path.join(root, '.agent-runs/fixture/raw-detail-results'),
  reporter: [['list']],
  use: { baseURL: `http://127.0.0.1:${port}`, locale: 'ko-KR', timezoneId: 'Asia/Seoul',
    viewport: { width: 1440, height: 900 }, trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  webServer: {
    command: `"${python}" scripts/dev/user_report_fixture.py --data-dir ${data} --port ${port}`,
    cwd: root, url: `http://127.0.0.1:${port}/health`, reuseExistingServer: false, timeout: 60000,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
    { name: 'firefox', use: { ...devices['Desktop Firefox'], viewport: { width: 1440, height: 900 } } },
  ],
});
