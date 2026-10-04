import { defineConfig, devices } from '@playwright/test';
import * as path from 'path';

const root = path.resolve(__dirname, '../..');
const data = '.agent-runs/refactoring-implementation/remount-browser-data';
process.env.SR_FIXTURE_DIR = path.join(root, data);
export default defineConfig({
  testDir: './specs', testMatch: 'refactoring-contracts.spec.ts', workers: 1, retries: 0, timeout: 60000,
  outputDir: path.join(root, '.agent-runs/refactoring-implementation/remount-browser-results'),
  reporter: [['list'], ['json', {outputFile: path.join(root, '.agent-runs/refactoring-implementation/remount-browser-results.json')}]],
  use: {baseURL: 'http://127.0.0.1:18883', locale: 'ko-KR', timezoneId: 'Asia/Seoul', trace: 'retain-on-failure'},
  webServer: {command: `.venv/bin/python scripts/dev/user_report_fixture.py --data-dir ${data} --port 18883`,
    cwd: root, url: 'http://127.0.0.1:18883/health', reuseExistingServer: false, timeout: 60000},
  projects: [{name: 'chromium', use: {...devices['Desktop Chrome']}}, {name: 'firefox', use: {...devices['Desktop Firefox']}}, {name: 'webkit', use: {...devices['Desktop Safari']}}],
});
