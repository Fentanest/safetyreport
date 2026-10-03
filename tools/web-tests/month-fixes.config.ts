import { defineConfig, devices } from '@playwright/test';
import * as path from 'path';
const root=path.resolve(__dirname,'../..');
const dir=path.join(root,'.agent-runs/month-fixes-20261003');
const broad=process.env.SR_MONTH_FIXES_BROAD==='1';
const port=Number(process.env.SR_MONTH_FIX_PORT||(broad?18724:18723));
const suite=broad?'broad':process.env.SR_MONTH_FIX_RUN||'browser';
export default defineConfig({
  testDir:'./specs', testMatch:broad?/(?:stats-renewal|stats-layout|stats-drilldown|stats-top|dashboard-cards|fixture-smoke|list-interactions|list-empty-state|settings-pages|ops-pages|map-page|editor-override|duplicates-manage)\.spec\.ts/:/month-fixes.spec.ts/,
  outputDir:path.join(dir,suite+'-results'),workers:1,retries:0,timeout:45000,
  reporter:[['list'],['json',{outputFile:path.join(dir,suite+'.json')}]],
  use:{baseURL:`http://127.0.0.1:${port}`,locale:'ko-KR',timezoneId:'Asia/Seoul',
       viewport:{width:1920,height:1080},trace:'retain-on-failure',screenshot:'only-on-failure'},
  projects:[{name:'chromium',use:{...devices['Desktop Chrome']}},{name:'firefox',use:{...devices['Desktop Firefox']}}],
  webServer:{command:`.venv/bin/python scripts/dev/user_report_fixture.py --data-dir .agent-runs/month-fixes-20261003/${broad?'broad':suite}-fixture --port ${port} ${broad?'':'--review-fixture'}`,
             cwd:root,url:`http://127.0.0.1:${port}/health`,reuseExistingServer:false,timeout:60000},
});
