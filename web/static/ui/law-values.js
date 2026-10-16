/* Shared law TEXT reader. No HTML is emitted by this module. */
(function(root) {
'use strict';
const aliases = {"장애인등편의법": "장애인·노인·임산부 등의 편의증진 보장에 관한 법률", "장애인등편의증진보장에관한법": "장애인·노인·임산부 등의 편의증진 보장에 관한 법률", "장애인노인임산부등의편의증진보장에관한법률": "장애인·노인·임산부 등의 편의증진 보장에 관한 법률", "환경친화적자동차의개발및보급촉진에관한법률": "환경친화적 자동차의 개발 및 보급 촉진에 관한 법률", "도로교통법": "도로교통법", "장애인복지법": "장애인복지법", "자동차관리법": "자동차관리법", "질서위반행위규제법": "질서위반행위규제법", "행정절차법": "행정절차법", "국세징수법": "국세징수법", "폐기물관리법": "폐기물관리법", "자동차손해배상보장법": "자동차손해배상보장법", "건설기계관리법": "건설기계관리법", "옥외광고물등의관리와옥외광고산업진흥에관한법률": "옥외광고물등의관리와옥외광고산업진흥에관한법률", "소음진동관리법": "소음진동관리법", "소음·진동관리법": "소음·진동관리법", "도로법": "도로법", "장애인등의편의증진보장에관한법": "장애인·노인·임산부 등의 편의증진 보장에 관한 법률", "장애인노인임산부등의편의증진보장에관한법": "장애인·노인·임산부 등의 편의증진 보장에 관한 법률", "교통약자이동편의증진법": "교통약자이동편의증진법", "친환경자동차법": "환경친화적 자동차의 개발 및 보급 촉진에 관한 법률", "형법": "형법", "교통단속처리지침": "교통단속 처리지침", "자동차등록번호판등의기준에관한고시": "자동차 등록번호판 등의 기준에 관한 고시", "고양시폐기물관리에관한조례": "고양시 폐기물관리에 관한 조례", "부천시폐기물관리에관한조례": "부천시 폐기물관리에 관한 조례", "자동차튜닝에관한규정": "자동차 튜닝에 관한 규정", "2025년경찰청공익신고접수기준표": "2025년 경찰청 공익신고 접수기준표"};
const keyPattern = /^(.+?)제?([0-9]+)조(의[0-9]+)?(?:제?([0-9]+)항)?(?:제?([0-9]+)호(의[0-9]+)?)?(?:([가-하])목)?$/;
// A law name never contains another article or a list separator (same rule as law_values.py/.dart).
const badName = /[,&]|[0-9]조/;
const cp1252 = {128:8364,130:8218,131:402,132:8222,133:8230,134:8224,135:8225,136:710,137:8240,138:352,139:8249,140:338,142:381,145:8216,146:8217,147:8220,148:8221,149:8226,150:8211,151:8212,152:732,153:8482,154:353,155:8250,156:339,158:382,159:376};
function decode(value) {
 return String(value == null ? '' : value).replace(/&(?:#[xX][0-9a-fA-F]+|#[0-9]+|[a-zA-Z]+);/g, token => {
  if (typeof document !== 'undefined') { const el=document.createElement('textarea'); el.innerHTML=token; return el.value; }
  const named={'&quot;':'"', '&apos;':"'", '&amp;':'&', '&nbsp;':' ', '&middot;':'·', '&lsquo;':'‘', '&rsquo;':'’', '&ldquo;':'“', '&rdquo;':'”', '&lt;':'<', '&gt;':'>'};
  if(named[token])return named[token];
  if(token[1]==='#'){const hex=/^&#x/i.test(token);const n=parseInt(token.slice(hex?3:2,-1),hex?16:10);if(n===0)return '�';if(cp1252[n])return String.fromCodePoint(cp1252[n]);return n<=0x10ffff&&!(n>=0xd800&&n<=0xdfff)?String.fromCodePoint(n):'�';}
  return token;
 });
}
function compact(value) { return decode(value).normalize('NFKC').replace(/[\s\u0085\u001c-\u001f·ㆍ･・ᆞ]/g, ''); }
function structured(text) { const m=keyPattern.exec(text); if(!m||m[1]==='제'||/^[0-9]+$/.test(m[1])||badName.test(m[1]))return null; return m; }
function lawName(name) {
 let suffix='';
 name=name.replace(/[「」『』【】\[\]“”‘’"']/g,'').replace(/\((?:약칭|이하같음|경찰청지침)\)/g,'');
 for (const s of ['시행규칙','시행령']) { if(name.endsWith(s)){suffix=s;name=name.slice(0,-s.length);break;} }
 name=name.replace(/[「」『』【】\[\]“”‘’"']/g,'').replace(/\((?:약칭|이하같음|경찰청지침)\)/g,'');
 if(!name)return '';
 return (Object.prototype.hasOwnProperty.call(aliases,name)?aliases[name]:name)+(suffix?' '+suffix:'');
}
function canonical(value) {
 const raw = String(value == null ? '' : value).trim(); const m = structured(compact(raw));
 if (!m) return raw;
 const name=lawName(m[1]); if(!name)return raw;
 return name+' 제'+m[2]+'조'+(m[3]||'')+(m[4]?'제'+m[4]+'항':'')+(m[5]?'제'+m[5]+'호':'')+(m[6]||'')+(m[7]?m[7]+'목':'');
}
const readCache=new Map(), matchCache=new Map();
function read(value) { const cacheKey=String(value == null ? '' : value); if(readCache.has(cacheKey))return readCache.get(cacheKey); const result=[...new Set(String(value == null ? '' : value).split(/\r\n?|\n|\u2028|\u2029/).filter(x=>x.trim()).map(canonical))]; if(readCache.size>=2048)readCache.delete(readCache.keys().next().value); Object.freeze(result);readCache.set(cacheKey,result);return result; }
function ancestors(key) {
 const m=structured(compact(key)); if(!m||!key.includes(' 제'))return [key];
 let stem=key.split(' 제')[0]+' 제'+m[2]+'조'+(m[3]||''); const values=[key,stem];
 if(m[4]){stem+='제'+m[4]+'항';values.push(stem);}
 if(m[5])values.push(stem+'제'+m[5]+'호'+(m[6]||''));
 return [...new Set(values)];
}
function term(keys, text, partial) {
 if(keys.includes(text.trim()))return true;
 const aq=/^제?([0-9]+)조(의[0-9]+)?(?:제?([0-9]+)항)?(?:제?([0-9]+)호(의[0-9]+)?)?(?:([가-하])목)?$/.exec(compact(text));
 if(aq){const tail='제'+aq[1]+'조'+(aq[2]||'')+(aq[3]?'제'+aq[3]+'항':'')+(aq[4]?'제'+aq[4]+'호':'')+(aq[5]||'')+(aq[6]?aq[6]+'목':'');return keys.some(k=>ancestors(k).some(p=>p===tail||p.endsWith(' '+tail)));}
 const query=canonical(text), folded=compact(query), isStructured=!!structured(folded);
 let nq=folded.replace(/[「」『』【】\[\]“”‘’"']/g,''), suffix='';
 for(const s of ['시행규칙','시행령']){if(nq.endsWith(s)){suffix=s;nq=nq.slice(0,-s.length);break;}}
 const name=Object.prototype.hasOwnProperty.call(aliases,nq) ? aliases[nq]+(suffix?' '+suffix:'') : null;
 return keys.some(key=>key===query||(isStructured&&ancestors(key).includes(query))||(name&&key.startsWith(name+' 제'))||(partial&&!isStructured&&!name&&compact(key).toLowerCase().includes(folded.toLowerCase())));
}
function matches(value,query,partial) {
 const key=JSON.stringify([value==null?'':String(value),query==null?'':String(query),!!partial]);
 if(matchCache.has(key))return matchCache.get(key);const result=matchesUncached(value,query,partial);
 if(matchCache.size>=16384)matchCache.delete(matchCache.keys().next().value);matchCache.set(key,result);return result;
}
function matchesUncached(value, query, partial) {
 const text=String(query == null ? '' : query).trim(); if(!text)return true;
 const keys=read(value); if(text==='__없음__')return !keys.length;
 // A query equal to one stored key matches it whole, even if that raw key contains ',' or '&'.
 if(keys.includes(text))return true;
 if(/[\r\n\u2028\u2029]/.test(text))return read(text).every(q=>term(keys,q,partial));
 const groups=text.split(',').map(g=>g.split('&').map(x=>x.trim()).filter(Boolean)).filter(g=>g.length);
 return !groups.length||groups.some(g=>g.every(q=>term(keys,q,partial)));
}
// Stored-key membership (statistics/detail links): one key per line = AND, no ','/'&' split, no name prefix.
// A row belongs to K when one of its keys is K or a descendant paragraph/item (same rule as law_memberships).
function matchesKeys(value, query) {
 const text=String(query == null ? '' : query).trim(); if(!text)return true;
 const keys=read(value); if(text==='__없음__')return !keys.length;
 const parents=new Set(); keys.forEach(k=>ancestors(k).forEach(p=>parents.add(p)));
 return read(text).every(k=>parents.has(k));
}
root.SrLawValues={canonical,read,matches,matchesKeys,ancestors};
})(typeof window==='undefined'?globalThis:window);
