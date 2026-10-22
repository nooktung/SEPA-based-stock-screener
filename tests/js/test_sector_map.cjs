const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('src/web/static/app.js','utf8');
const c=vm.createContext({sectorState:{data:{as_of:'2026-10-21'}}});
for(const name of ['sectorMapRects','sectorMapCurrent','sectorMapColor','sectorMapChange']){
 const fn=source.match(new RegExp('function '+name+'\\([^]*?\\n\\}'));assert.ok(fn,name);vm.runInContext(fn[0],c);
}
// Uneven weights, tiny boxes and resizing must preserve area and avoid overlap.
for(const [w,h] of [[1000,600],[350,530],[1,1],[600,1000]]){
 const items=Array.from({length:97},(_,i)=>({id:i,weight:(i+1)**2}));
 const snapshot=JSON.stringify(items),total=items.reduce((n,r)=>n+r.weight,0);
 const rs=c.sectorMapRects(items,7,11,w,h);
 assert.equal(rs.length,items.length);assert.equal(JSON.stringify(items),snapshot);
 let area=0;
 for(const r of rs){
  assert.ok(r.x>=7-1e-8&&r.y>=11-1e-8&&r.x+r.w<=7+w+1e-8&&r.y+r.h<=11+h+1e-8);
  assert.ok(Math.abs(r.w*r.h/(w*h)-r.weight/total)<1e-10);area+=r.w*r.h;
 }
 assert.ok(Math.abs(area-w*h)<1e-6);
 for(let i=0;i<rs.length;i++)for(let j=i+1;j<rs.length;j++){
  const a=rs[i],b=rs[j];assert.ok(Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x)<1e-8||Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y)<1e-8);
 }
 assert.equal(JSON.stringify(c.sectorMapRects(items,7,11,w,h)),JSON.stringify(rs));
}
assert.equal(c.sectorMapRects([{weight:0},{weight:-1},{weight:NaN},{weight:Infinity}],0,0,10,10).length,0);
assert.equal(c.sectorMapRects([{weight:1}],0,0,0,10).length,0);
const current=(change,value=1)=>({date:'2026-10-21',change,value});
assert.equal(c.sectorMapChange([current(4,3),current(-4,1),{...current(90,100),date:'2026-10-20'},{...current(90,100),flagged:true},current(null,100)]),2);
assert.equal(c.sectorMapChange([current(4,0)]),null);
assert.equal(c.sectorMapColor(null,'ceiling'),'#465164');
assert.equal(c.sectorMapColor(0),'#f1ae55');
assert.equal(c.sectorMapColor(-3),'#c93d36');
assert.equal(c.sectorMapColor(3),'#009f66');
assert.equal(c.sectorMapColor(6.99),'#009f66'); // a large rise alone cannot imply ceiling
assert.equal(c.sectorMapColor(6.99,'ceiling'),'#6747bf');
assert.equal(c.sectorMapColor(-6.99,'floor'),'#00a8cf');
console.log('treemap: area, bounds, non-overlap, proportions, resize and stale-data exclusions passed');

// Industry labels form one list; a duplicate name uses the classified cohort.
c.fold=s=>String(s).normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
c.dmy=s=>s;
for(const name of ['sectorGroups','sectorTrendChart']){
 const fn=source.match(new RegExp('function '+name+'\\([^]*?\\n\\}'));assert.ok(fn,name);vm.runInContext(fn[0],c);
}
const unified=c.sectorGroups([{id:'t',name:'Bank',kind:'theme'},{id:'i',name:'Bank',kind:'industry'},{id:'p',name:'Ports',kind:'theme'}]);
assert.equal(unified.length,2);assert.equal(unified[0].id,'i');
const trend=c.sectorTrendChart([{date:'2026-10-19',relative:2},{date:'2026-10-20',relative:null},{date:'2026-10-21',relative:-1},{date:'2026-10-22',relative:0}]);
assert.ok(!trend.includes('NaN')&&!trend.includes('Infinity'));assert.ok(trend.includes('M24.0'));
console.log('industry drawer: unified cohorts and valid trend geometry passed');
