const {chromium, firefox, expect} = require('../../../../tools/web-tests/node_modules/@playwright/test');
const fs = require('fs');
const path = require('path');
const root = path.resolve(__dirname, '../../../..');
const evidence = path.join(root, 'docs/implementation/official-account-binding-20261006/evidence');
const data = path.join(root, '.agent-runs/official-binding-rc/browser-data');
(async () => {
 const engine = process.argv[2] || 'chromium';
 const browser = await ({chromium,firefox}[engine]).launch({headless:true});
 const page = await browser.newPage({viewport:{width:1440,height:900}});
 const errors=[];
 page.on('pageerror', e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:18806/login');
 await page.locator('[name=username]').fill('fixture-admin');
 await page.locator('[name=password]').fill('fixture-pass-1234');
 await page.locator('button[type=submit]').click();
 await page.waitForURL('http://127.0.0.1:18806/');
 for (const mode of ['old','unbound']) {
  fs.writeFileSync(path.join(data,'mode'),mode);
  const result = await page.evaluate(async()=>{
   const response = await fetch('/settings/official-account/retry',{method:'POST'});
   return response.json();
  });
  expect(result.data.can_enter).toBe(true);
  await page.goto('http://127.0.0.1:18806/stats');
  await expect(page).toHaveURL('http://127.0.0.1:18806/stats');
 }
 fs.writeFileSync(path.join(data,'mode'),'ok');
 await page.goto('http://127.0.0.1:18806/settings/');
 await expect(page.locator('#fld_username')).toHaveValue('Fixture.Official');
 await page.locator('#fld_username').fill('browser-new-account');
 await page.locator('#mainSettingsForm button[type=submit]').click();
 await expect(page.getByText('기존 데이터가 지워집니다.', {exact:false})).toBeVisible();
 await expect(page.locator('.modal.show')).toHaveCSS('opacity', '1');
 await page.screenshot({path:path.join(evidence,`${engine}-confirm-light.png`), fullPage:false, animations:'disabled'});
 await page.locator('.modal.show').getByRole('button',{name:'취소',exact:true}).click();
 await expect(page.locator('#officialAccountConfirm')).toHaveValue('');
 await page.locator('#mainSettingsForm button[type=submit]').click();
 await page.locator('[data-sr-confirm=ok]').click();
 await page.waitForURL('**/settings/?saved=true');
 await expect(page.locator('#fld_username')).toHaveValue('browser-new-account');
 const {execFileSync} = require('child_process');
 const proof = JSON.parse(execFileSync(path.join(root,'.venv/bin/python'), ['-c', `
import sqlite3, pathlib, json
root=pathlib.Path(${JSON.stringify(data)})
backups=sorted((pathlib.Path((root/'fake-data-path.txt').read_text())/'backups').glob('*.db'))
assert len(backups)==1, backups
with sqlite3.connect(backups[0]) as backup, sqlite3.connect(root/'data.db') as live:
 assert backup.execute('pragma integrity_check').fetchone()[0]=='ok'
 tables=['mysafety','mysafetydetail_traffic','mysafetydetail_parking','mysafetydetail_other']
 before={name:backup.execute('select count(*) from "'+name+'"').fetchone()[0] for name in tables}
 after={name:live.execute('select count(*) from "'+name+'"').fetchone()[0] for name in tables}
 assert before==dict(zip(tables,[24,12,6,6])),before
 assert all(v==0 for v in after.values()),after
 assert live.execute("select value from mysafety_sync_meta where key='official_account_dataset_key'").fetchone() is None
 print(json.dumps({'backup_integrity':'ok','backup_count':len(backups),'before':before,'after':after,'personal_db_official_key':False}))
`], {encoding:'utf8'}));
 fs.writeFileSync(path.join(root,`.agent-runs/official-binding-rc/${engine}-backup-proof.json`),JSON.stringify(proof,null,2));

 let config=fs.readFileSync(path.join(data,'config.ini'),'utf8');
 fs.writeFileSync(path.join(data,'config.ini'),config.replace('username = browser-new-account','username = manually-changed'));
 await page.goto('http://127.0.0.1:18806/stats');
 await page.waitForURL('**/settings/');
 await expect(page.getByRole('alert').filter({hasText:'연결된 안전신문고 계정과 설정이 다릅니다'})).toBeVisible();
 await page.screenshot({path:path.join(evidence,`${engine}-mismatch-settings.png`), animations:'disabled'});
 await page.goto('http://127.0.0.1:18806/onboarding/rebuild');
 await page.waitForURL('**/settings/');
 fs.writeFileSync(path.join(data,'config.ini'),config);
 fs.writeFileSync(path.join(data,'mode'),'offline');
 await page.evaluate(async()=>fetch('/settings/official-account/retry',{method:'POST'}));
 await page.goto('http://127.0.0.1:18806/stats');
 await page.waitForURL('**/onboarding/cloud');
 await expect(page.locator('#cloudMessage')).toHaveText('클라우드에 연결할 수 없습니다. 잠시 후 이용해 주세요');
 await page.screenshot({path:path.join(evidence,`${engine}-cloud-light.png`)});
 await page.setViewportSize({width:390,height:844});
 await page.evaluate(()=>document.documentElement.setAttribute('data-bs-theme','dark'));
 await page.screenshot({path:path.join(evidence,`${engine}-cloud-dark-mobile.png`)});
 await expect(page.locator('#cloudRetryStatus')).toContainText('자동 재시도를 마쳤습니다', {timeout:40000});
 await expect(page.locator('#cloudRetry')).toBeEnabled();
 fs.writeFileSync(path.join(data,'mode'),'ok');
 await page.locator('#cloudRetry').click();
 await page.waitForURL('http://127.0.0.1:18806/');
 fs.writeFileSync(path.join(data,'mode'),'taken');
 await page.goto('http://127.0.0.1:18806/settings/');
 await page.locator('#fld_username').fill('browser-taken-account');
 await page.locator('#mainSettingsForm button[type=submit]').click();
 await page.locator('[data-sr-confirm=ok]').click();
 await page.waitForURL('**/settings/?saved=true');
 await expect(page.getByRole('alert').filter({hasText:'운영자에게 문의'})).toBeVisible();
 await page.goto('http://127.0.0.1:18806/');
 await page.waitForURL('**/settings/');
 fs.writeFileSync(path.join(data,'mode'),'ok');
 await page.evaluate(async()=>fetch('/settings/official-account/retry',{method:'POST'}));
 const logout = await page.evaluate(async()=>{
  const response = await fetch('/settings/community/logout', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:'DELETE_MY_REPORTS'})});
  return response.json();
 });
 expect(logout.gate.state).toBe('kakao_required');
 await page.goto('http://127.0.0.1:18806/');
 await page.waitForURL('**/onboarding/community?**');
 await expect(page.locator('#obContinueBtn')).toBeHidden();
 await expect(page.locator('#gateSummary')).toContainText('카카오');
 await page.goto('http://127.0.0.1:18806/settings/');
 await page.waitForURL('**/onboarding/community?**');
 await expect(page.locator('#obContinueBtn')).toBeHidden();
 await page.screenshot({path:path.join(evidence,`${engine}-kakao-required.png`)});
 expect(errors).toEqual([]);
 console.log(JSON.stringify({browser:engine,flowsPassed:9,errors}));
 fs.writeFileSync(path.join(root,`.agent-runs/official-binding-rc/${engine}-browser-errors.json`),JSON.stringify(errors,null,2));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1);});
