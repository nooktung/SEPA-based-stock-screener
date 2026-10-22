const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('src/web/static/learn.js','utf8');
// Evaluate the shipped geometry and educational data, without altering production exports.
const marker=' const modules=';
assert.ok(source.includes(marker));
const prefix=source.slice(0,source.indexOf(marker));
const context=vm.createContext({document:{getElementById:()=>null}});
vm.runInContext(prefix+' globalThis.lessons={basics:BASICS,candles:CANDLES.filter(x=>!x.group),charts:CHARTS.filter(x=>!x.group),sepa:SEPA}; globalThis.drawCandle=candleSvg;globalThis.drawChart=chartSvg;})();',context);
const {basics,candles,charts,sepa}=context.lessons;
assert.equal(basics.length,4);
assert.ok(!source.includes("function quiz(")&&!source.includes("data-candle-preset")&&!source.includes("learn-close"));
assert.ok(candles.length>=18&&charts.length>=12&&sepa.length===6);
for(const item of candles){
 for(const [o,h,l,c] of [...(item.svg.ctx||[]),...item.svg.pat])assert.ok(l<=Math.min(o,c)&&h>=Math.max(o,c),item.name);
 const svg=context.drawCandle(item.svg);
 assert.ok(!svg.includes('<text')&&!svg.includes('NaN')&&!svg.includes('Infinity'),item.name);
}
for(const item of [...charts,...sepa,...basics.filter(x=>!x.candle)]){
 assert.ok(item.how&&item.means&&item.confirm,item.name);
 const svg=context.drawChart(item.svg);
 assert.ok(!svg.includes('<text')&&!svg.includes('NaN')&&!svg.includes('Infinity'),item.name);
 // All explanatory labels are rendered outside the SVG, so formula text cannot spill onto neighbors.
 assert.ok(svg.includes('viewBox="0 0 480 260"'));
}
console.log(`learn: ${candles.length} candle lessons, ${charts.length} chart lessons, ${sepa.length} SEPA lessons; valid OHLC and text-free diagrams passed`);
