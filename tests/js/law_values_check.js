const fs=require('fs');require('../../web/static/ui/law-values.js');
const vectors=JSON.parse(fs.readFileSync('contracts/law-search-vectors.json','utf8'));
let failed=[];for(const c of vectors.cases){const got=globalThis.SrLawValues.matches(c.value,c.query,false);if(got!==c.expected)failed.push({name:c.name,actual:got,expected:c.expected});}
console.log(JSON.stringify({cases:vectors.cases.length,failed}));if(failed.length)process.exitCode=1;
