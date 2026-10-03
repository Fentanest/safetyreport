import { defineConfig, devices } from '@playwright/test';
import * as path from 'path';
const root = path.resolve(__dirname, '../..');
const run = process.env.SR_REVIEW_RUN || 'complete';
export default defineConfig({
  testDir: './specs', testMatch: /user-reports.spec.ts/,
  outputDir: path.join(root, '.agent-runs/v3-user-reports/browser-results',run),
  workers: 1, retries: 0, timeout: 45000,
  reporter: [['list'], ['json', {outputFile:path.join(root,'.agent-runs/v3-user-reports',`browser-${run}.json`)}]],
  use: {baseURL:'http://127.0.0.1:18703',locale:'ko-KR',timezoneId:'Asia/Seoul',trace:'retain-on-failure',screenshot:'only-on-failure'},
  projects: [{name:'chromium',use:{...devices['Desktop Chrome']}},{name:'firefox',use:{...devices['Desktop Firefox']}}],
  webServer: {command:'.venv/bin/python scripts/dev/user_report_fixture.py --data-dir .agent-runs/v3-user-reports/browser-fixture --port 18703',
    cwd:root,url:'http://127.0.0.1:18703/health',reuseExistingServer:true,timeout:60000},
});
