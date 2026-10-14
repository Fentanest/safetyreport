import {defineConfig,devices} from '@playwright/test';
import * as path from 'path';
const root=path.resolve(__dirname,'../..');const port=Number(process.env.SR_LAW_TEST_PORT||18868);
const python=process.env.SR_PYTHON||path.join(root,'.venv/bin/python');
export default defineConfig({
 testDir:'./specs',testMatch:'law-search.spec.ts',workers:1,retries:0,timeout:60000,
 outputDir:path.join(root,'.agent-runs/b-implementation/browser-results'),
 reporter:[['list'],['json',{outputFile:path.join(root,'.agent-runs/b-implementation/browser-results.json')}]],
 use:{baseURL:`http://127.0.0.1:${port}`,locale:'ko-KR',timezoneId:'Asia/Seoul',trace:'retain-on-failure',screenshot:'only-on-failure'},
 webServer:{command:`"${python}" scripts/dev/user_report_fixture.py --data-dir .agent-runs/b-implementation/browser-fixture --port ${port} --law-fixture`,cwd:root,url:`http://127.0.0.1:${port}/health`,reuseExistingServer:false,timeout:60000},
 projects:[{name:'chromium',use:{...devices['Desktop Chrome']}},{name:'firefox',use:{...devices['Desktop Firefox']}}],
});
