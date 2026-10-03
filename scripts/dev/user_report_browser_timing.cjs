// Real browser navigation timing. Generated output belongs to .agent-runs, not product data.
const {chromium}=require('../../tools/web-tests/node_modules/@playwright/test');
const fs=require('fs');
(async()=>{
  const origin=process.argv[2], output=process.argv[3];
  const browser=await chromium.launch();
  const page=await browser.newPage({viewport:{width:1366,height:900}});
  const result={origin,population:24,measurements:[]};
  try {
    await page.goto(origin+'/login');
    await page.locator('#username').fill('fixture-admin'); await page.locator('#password').fill('fixture-pass-1234');
    await Promise.all([page.waitForURL(origin+'/'),page.getByRole('button',{name:'로그인'}).click()]);
    for(const target of ['/crawl/','/','/stats','/stats/map']) {
      let requestAt=0; const listen=r=>{if(r.isNavigationRequest()&&r.url()===origin+target)requestAt=Date.now();};
      page.on('request',listen);
      const clickAt=Date.now();
      const link=page.locator('a[href="'+target+'"]').first();
      const physical=await link.count()>0;
      const response=physical ? (await Promise.all([page.waitForNavigation({waitUntil:'domcontentloaded'}),link.click()]))[0]
                              : await page.goto(origin+target,{waitUntil:'domcontentloaded'});
      const domAt=Date.now();
      if(target==='/stats') await page.locator('.stats-pane:visible .dataTables_wrapper').waitFor();
      if(target.includes('/stats')) await page.locator('.leaflet-marker-icon').first().waitFor();
      const readyAt=Date.now();
      result.measurements.push({target,physical_menu_click:physical,action_to_request_ms:requestAt-clickAt,action_to_dom_ms:domAt-clickAt,
        action_to_ready_ms:readyAt-clickAt,server_timing:response.headers()['server-timing']||null,
        ...await page.evaluate(()=>({navigation:performance.getEntriesByType('navigation')[0].toJSON(),
          resources:performance.getEntriesByType('resource').filter(e=>/map\/points|stats\/content|sunwi|tile/.test(e.name)).map(e=>e.toJSON()),
          marks:performance.getEntriesByType('mark').map(e=>e.toJSON()),
          heap_bytes:performance.memory?performance.memory.usedJSHeapSize:null}))});
      page.off('request',listen);
    }
    fs.writeFileSync(output,JSON.stringify(result,null,2));
    console.log(result.measurements.map(x=>({target:x.target,ready_ms:x.action_to_ready_ms,ttfb_ms:x.navigation.responseStart-x.navigation.requestStart})));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
