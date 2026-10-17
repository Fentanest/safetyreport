import { defineConfig, devices } from '@playwright/test';
import * as path from 'path';

// DB 최적화 하단 표시줄 전용 브라우저 스모크. 기본 playwright.config.ts 는 로컬 통합 스택 키가 없으면
// 커뮤니티 필수 게이트에 막힌 fixture 로 뜬다 — category-split.config.ts 와 같이
// user_report_fixture.py(프로세스 안 가짜 중앙 인증)로 게이트를 실제로 통과한 화면에서 검사한다.
const root = path.resolve(__dirname, '../..');
const data = '.agent-runs/fixture/db-optimize-bar';
const port = Number(process.env.SR_TEST_PORT || 18961);
const python = process.env.SR_PYTHON ?? '/home/better0101/projects/safetyreport/.agent-runs/venv313/bin/python';

export default defineConfig({
  testDir: './specs',
  testMatch: '**/db-optimize-bar.spec.ts',
  workers: 1,
  retries: 0,
  timeout: 60000,
  outputDir: path.join(root, '.agent-runs/fixture/db-optimize-bar-results'),
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
