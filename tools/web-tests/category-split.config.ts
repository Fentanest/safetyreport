import { defineConfig, devices } from '@playwright/test';
import * as path from 'path';

// Stage 4B(5분류 UI) 전용 브라우저 검수. 기본 playwright.config.ts 는 로컬 통합 스택 키가 없으면
// 커뮤니티 필수 게이트에 막힌 fixture 로 뜬다(docs/testing/web-ui-test-plan.md §8) — 대신 다른 전용 설정들처럼
// user_report_fixture.py(프로세스 안 가짜 중앙 인증)로 게이트를 실제로 통과한 화면에서 검사한다.
const root = path.resolve(__dirname, '../..');
const data = '.agent-runs/fixture/sonnet-s4ui-category-split';
const port = Number(process.env.SR_TEST_PORT || 18960);
const python = process.env.SR_PYTHON ?? '/home/better0101/projects/safetyreport/.venv/bin/python';

export default defineConfig({
  testDir: './specs',
  testMatch: '**/category-split.spec.ts',
  workers: 1,
  retries: 0,
  timeout: 60000,
  outputDir: path.join(root, '.agent-runs/fixture/sonnet-s4ui-category-split-results'),
  reporter: [['list']],
  use: { baseURL: `http://127.0.0.1:${port}`, locale: 'ko-KR', timezoneId: 'Asia/Seoul',
    viewport: { width: 1440, height: 900 }, trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  webServer: {
    command: `"${python}" scripts/dev/user_report_fixture.py --data-dir ${data} --port ${port}`,
    cwd: root, url: `http://127.0.0.1:${port}/health`, reuseExistingServer: false, timeout: 60000,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
  ],
});
