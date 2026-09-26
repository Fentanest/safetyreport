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

// 2026-09-26 필수 게이트: 화면은 카카오 인증 + 공유 동의가 있어야 열린다. 로컬 통합 스택(127.0.0.1)이 떠 있으면
// SR_COMMUNITY_PUBLISHABLE_KEY 로 fixture 에 세션·동의를 넣고 시작한다(scripts/dev/community_qa_session.py).
// 없으면 fixture 는 게이트에 막힌 상태로 뜬다(온보딩 화면 검수용).
const communityKey = process.env.SR_COMMUNITY_PUBLISHABLE_KEY;
const serve = `"${python}" scripts/dev/fixture_server.py serve --data-dir "${dataDir}" --port ${port}`;
const serverCommand = communityKey
  ? `"${python}" scripts/dev/fixture_server.py seed --data-dir "${dataDir}" --reset && `
    + `"${python}" scripts/dev/community_qa_session.py --data-dir "${dataDir}" --publishable-key "${communityKey}" `
    + `--choice ${process.env.SR_COMMUNITY_CHOICE ?? 'A'} --consent --official-id fixture-official --rebuild-done && ${serve}`
  : `${serve} --reset`;

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
    command: serverCommand,
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
