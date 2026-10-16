import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync(new URL('../../src/web/static/app.js', import.meta.url), 'utf8');
const context = vm.createContext({ URL, location: { href: 'https://example.test/' },
  SITE: { detail: ['AAA'] }, noteVersion() {}, toLogin() { throw new Error('login'); },
  fetch: async () => new Response('{"ok":true}', { headers: { 'Content-Type': 'application/json' } }) });
vm.runInContext(source.slice(source.indexOf('function staticPath'), source.indexOf('// The web copy')), context);
vm.runInContext(source.slice(source.indexOf('async function get('), source.indexOf('// A tab left open')), context);
assert.equal((await vm.runInContext('get("/api/ticker?ticker=AAA")', context)).ok, true);
context.fetch = () => { throw new Error('should not fetch missing ticker'); };
await assert.rejects(vm.runInContext('get("/api/ticker?ticker=BBB")', context), /BBB.*chưa có/);
context.fetch = async () => new Response('<!doctype html>', { headers: { 'Content-Type': 'text/html' } });
await assert.rejects(vm.runInContext('get("/api/ticker?ticker=AAA")', context), /chưa có dữ liệu/);
context.fetch = async () => new Response('<!doctype html>', { headers: { 'Content-Type': 'application/json' } });
await assert.rejects(vm.runInContext('get("/api/ticker?ticker=AAA")', context), /không hợp lệ/);
context.fetch = async () => new Response('', { status: 401 });
await assert.rejects(vm.runInContext('get("/api/ticker?ticker=AAA")', context), /login/);
vm.runInContext(source.slice(source.indexOf('function seedProjectionGrid'), source.indexOf('class FillPrimitive')), context);
const grids = [];
context.charts = Array.from({ length: 3 }, () => ({ addLineSeries: () => ({ setData: (data) => grids.push(data) }) }));
context.bars = [{ trade_date: '2026-10-14' }, { trade_date: '2026-10-15' }];
context.future = ['2026-10-16', '2026-10-19', '2026-10-20'];
vm.runInContext('seedProjectionGrid(charts, bars, future)', context);
assert.equal(grids.length, 3);
for (const grid of grids) {
  assert.equal(grid.length, 5);
  assert.equal(grid[3].time, '2026-10-19');
  assert.ok(grid.every(p => !('value' in p))); // no invented candle or indicator values
}
console.log('chart regressions: ok');
