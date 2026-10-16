const fs=require('fs');require('../../web/static/ui/law-values.js');
const vectors=JSON.parse(fs.readFileSync('contracts/law-search-vectors.json','utf8'));
const L=globalThis.SrLawValues;
let failed=[];
for(const c of vectors.cases){const got=L.matches(c.value,c.query,false);if(got!==c.expected)failed.push({name:c.name,actual:got,expected:c.expected});}
for(const c of vectors.canonical_cases){const got=L.canonical(c.value);if(got!==c.expected)failed.push({name:'canonical:'+c.name,actual:got,expected:c.expected});}
for(const c of vectors.key_cases){const got=L.matchesKeys(c.value,c.query);if(got!==c.expected)failed.push({name:'key:'+c.name,actual:got,expected:c.expected});}
// Bar counts come from the server; the web list (lawExact) must return the same rows for each bar filter.
for(const c of vectors.stats_cases){for(const bar of c.expected){const n=c.values.filter(v=>L.matchesKeys(v,bar.filter)).length;if(n!==bar.count)failed.push({name:'stats:'+c.name+':'+bar.name,actual:n,expected:bar.count});}}
const total=vectors.cases.length+vectors.canonical_cases.length+vectors.key_cases.length+vectors.stats_cases.length;
console.log(JSON.stringify({cases:total,failed}));if(failed.length)process.exitCode=1;
