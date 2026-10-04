import {test,expect,Page} from '@playwright/test';
async function login(page:Page){await page.goto('/login');await page.locator('#username').fill('fixture-admin');await page.locator('#password').fill('fixture-pass-1234');await Promise.all([page.waitForURL('**/'),page.locator('button[type=submit]').click()]);}

test('official links warm up with no-cors then open the exact target without external calls',async({page})=>{
 await login(page);
 const output=await page.evaluate(async()=>{
  const w=window as any,oldFetch=w.fetch,oldOpen=w.open,observed:any[]=[];
  try{w.fetch=async(url:any,options:any)=>{observed.push({url,options});return {ok:true}};w.open=(url:string,target:string)=>observed.push({opened:url,target});
   const a=document.createElement('a');a.href='https://www.safetyreport.go.kr/#mypage/mysafereport/FIXTURE';a.target='_blank';document.body.appendChild(a);a.click();await new Promise(r=>setTimeout(r,0));a.remove();return observed;
  }finally{w.fetch=oldFetch;w.open=oldOpen;}
 });
 expect(output).toEqual([{url:'https://www.safetyreport.go.kr/',options:{mode:'no-cors',cache:'no-store'}},{opened:'https://www.safetyreport.go.kr/#mypage/mysafereport/FIXTURE',target:'_blank'}]);
});

test('detail supplement fields and app fallback preserve the exact synthetic record',async({page})=>{
 await login(page);await page.clock.install();
 await page.evaluate(()=>{
  const w=window as any;w.__opened=[];w.open=(url:string)=>w.__opened.push(url);
  // Capture the scheme navigation without starting a real external application.
  document.addEventListener('click',e=>{const a=(e.target as Element).closest('a');if(a?.href.startsWith('appsafetyreport:')){w.__scheme=a.href;e.preventDefault();}},true);
  w.showReportDetail({'ID':'합성 ID','신고번호':'FIXTURE','신고명':'보완 fixture','보완횟수':2,'보완_요청자':'검수담당','보완_요청일시':'2026-10-01 12:30:00','보완_요청_내용':'첫 줄\n둘째 줄','보완_신고자_의견':'추가 의견','보완_미응답':'Y'});
 });
 await expect(page.locator('#rdSupplementsBadge')).toHaveText('2회');await expect(page.locator('#rdSupplementsBody')).toContainText('검수담당');await expect(page.locator('#rdSupplementsBody')).toContainText('미응답');await expect(page.locator('#rdSupplementsBody')).toContainText('첫 줄');await expect(page.locator('#rdSupplementsBody')).toContainText('추가 의견');
 await page.locator('#rdViewBtn').click();await page.clock.runFor(1201);
 const result=await page.evaluate(()=>({scheme:(window as any).__scheme,opened:(window as any).__opened}));
 expect(result.scheme).toContain('appsafetyreport://view?c_no='+encodeURIComponent('합성 ID'));
 expect(result.opened).toEqual(['https://www.safetyreport.go.kr/#mypage/mysafereport/'+encodeURIComponent('합성 ID')]);
});

test('watchlist adds an existing record, persists it and removes the same record',async({page})=>{
 await login(page);await page.goto('/watchlist');page.on('dialog',d=>d.accept());
 await page.locator('#addWatchlistIds').fill('SPP-2603-9000005');await Promise.all([page.waitForNavigation(),page.locator('#btnAddManual').click()]);
 await expect(page.locator('#watchlistTable')).toContainText('SPP-2603-9000005');await page.reload();
 const row=page.locator('#watchlistTable tbody tr').filter({hasText:'SPP-2603-9000005'});await expect(row).toHaveCount(1);
 await row.locator('.row-checkbox').check();await Promise.all([page.waitForNavigation(),page.locator('#btnRemoveWatchlist').click()]);await page.reload();await expect(page.locator('#watchlistTable')).not.toContainText('SPP-2603-9000005');
});

test('detail links retain search fields and proxy video plays then releases on close',async({page})=>{
 await login(page);
 await page.route('**/media/prepare?**',r=>r.fulfill({json:{status:'ready',ready:true}}));
 await page.route('**/media/proxy?**',r=>r.fulfill({path:require('path').resolve(__dirname,'../../../tests/fixtures/media/blue-1s.webm'),contentType:'video/webm'}));
 await page.evaluate(()=>{
  (window as any).showReportDetail({'ID':'fixture-video','category':'traffic','신고명':'합성 동영상','담당자':'합성담당','차량번호':'12가3456','위반법규':'법규 조합','위반장소':'합성 장소','첨부파일':'https://www.safetyreport.go.kr/fileDown/singo/fixture.webm'});
 });
 for(const [text,field]of [['합성담당','person'],['12가3456','car'],['법규 조합','law'],['합성 장소','location']]){
  const link=page.locator('#rdFields a').filter({hasText:text});const href=await link.getAttribute('href');const url=new URL(href!,'http://fixture.invalid');expect(url.pathname).toBe('/data/traffic');expect(url.searchParams.get(field)).toBe(text);
 }
 const video=page.locator('#rdVideoList video');await expect(video).toHaveAttribute('src',/^\/media\/proxy\?url=/);
 await video.evaluate(async(el:HTMLVideoElement)=>{el.muted=true;await el.play()});
 await expect.poll(()=>video.evaluate((el:HTMLVideoElement)=>el.currentTime)).toBeGreaterThan(0);
 await page.locator('#reportDetailModal .btn-close').click();await expect(page.locator('#reportDetailModal')).toBeHidden();
 await expect.poll(()=>video.evaluate((el:HTMLVideoElement)=>el.paused)).toBe(true);await expect(video).not.toHaveAttribute('src');
});

test('police, rating, poll and date filters select the exact fixture records and reset',async({page})=>{
 await login(page);await page.goto('/data/all');await expect(page.locator('#allTable')).toHaveAttribute('aria-busy','false');
 const source=await page.evaluate(()=> (window as any).jQuery('#allTable').DataTable().rows().data().toArray());
 const actual=()=>page.evaluate(()=> (window as any).jQuery('#allTable').DataTable().rows({search:'applied'}).data().toArray().map((r:any)=>r['신고번호']).sort());
 const compare=async(predicate:(r:any)=>boolean)=>{
  const expected=source.filter(predicate).map((r:any)=>r['신고번호']).sort();expect(expected.length).toBeGreaterThan(0);expect(await actual()).toEqual(expected);
 };
 const apply=()=>page.evaluate(()=>{const $=(window as any).jQuery;$('#advSearchForm').trigger($.Event('keydown',{key:'Enter'}));});
 await page.evaluate(()=>{(document.querySelector('#onlyPolice') as HTMLInputElement).checked=true;});await apply();await compare(r=>String(r['처리기관']).includes('경찰'));
 await page.evaluate(()=>{(document.querySelector('#onlyPolice') as HTMLInputElement).checked=false;(document.querySelector('#excludePolice') as HTMLInputElement).checked=true;});await apply();await compare(r=>!String(r['처리기관']).includes('경찰'));
 await page.evaluate(()=>{(document.querySelector('#excludePolice') as HTMLInputElement).checked=false;(document.querySelector('#searchPollStatus') as HTMLSelectElement).value='참여 완료';});await apply();await compare(r=>r['만족도조사여부']==='참여 완료');
 await page.evaluate(()=>{(document.querySelector('#searchPollStatus') as HTMLSelectElement).value='';const $=(window as any).jQuery;$('#searchRatingDropdown .multi-select-option').filter((_:number,el:Element)=>el.querySelector('span')?.textContent==='5점').trigger('click');});await apply();await compare(r=>Number(r['별점'])===5);
 await page.evaluate(()=>{const $=(window as any).jQuery;$('#searchRatingDropdown .multi-select-option').first().trigger('click');(document.querySelector('#searchReportDateStart') as HTMLInputElement).value='2026-03-01';(document.querySelector('#searchReportDateEnd') as HTMLInputElement).value='2026-03-31';});await apply();await compare(r=>String(r['신고일']).startsWith('2026-03-'));
 await page.evaluate(()=>{(document.querySelector('#advSearchForm') as HTMLFormElement).reset();(document.querySelector('#btnReset') as HTMLElement).click();});await expect.poll(actual).toEqual(source.map((r:any)=>r['신고번호']).sort());
});
