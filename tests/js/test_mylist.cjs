const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/web/static/app.js', 'utf8');
const state = {
  rows: [{ticker: 'FPT', trade_date: '2026-10-20', close: 100, tech: {rsi: 55}, note: 'private admin note'},
         {ticker: 'ABC', trade_date: '2026-10-19', close: 50, tech: {rsi: 40}}],
  market: [{ticker: 'FPT', trade_date: '2026-10-19', close: 90},
           {ticker: 'ABC', trade_date: '2026-10-20', close: 60},
           {ticker: 'XYZ', trade_date: '2026-10-20', close: 70}],
  mine: new Set(), mineNotes: new Map(),
};
let items = [];
const context = vm.createContext({state, SITE: {},
  post: async () => ({items}), esc: String, toast() {},
  loadMarket: async () => {}, loadStocks: () => {throw Error('Must not re-read cached list after a successful write');},
  setCounts() {}, renderMinePending() {}, renderTable() {}, syncDetailStar() {}, syncStars() {},
});
for (const name of ['mineRows', 'acceptMineList', 'toggleMine']) {
  const match = source.match(new RegExp('(?:async )?function ' + name + '\\([^]*?\\n\\}'));
  assert.ok(match, name);
  vm.runInContext(match[0], context);
}
(async () => {
  context.acceptMineList([{ticker: 'FPT'}, {ticker: 'ABC'}, {ticker: 'XYZ', note: 'my note'}, {ticker: 'NEW'}]);
  const rows = context.mineRows();
  assert.equal(rows.length, 4);
  assert.equal(rows[0].close, 100); // never regress to an older market snapshot
  assert.equal(rows[0].note, null); // do not show another account's note
  assert.equal(rows[1].close, 60);
  assert.equal(rows[1].state_date, '2026-10-19'); // old analysis must keep its date
  assert.equal(rows[2].close, 70); // unmonitored ticker appears immediately
  assert.equal(rows[2].trade_date, '2026-10-20');
  assert.equal(rows[2].note, 'my note');
  assert.equal(Object.keys(rows[2].tech).length, 0); // never invent an analysis
  assert.equal(rows[3].close, undefined);
  assert.equal(state.rows[1].close, 50); // preserve shared rows
  context.acceptMineList([]);
  items = [{ticker: 'XYZ'}];
  assert.equal(await context.toggleMine('XYZ'), true);
  assert.equal(context.mineRows()[0].close, 70);
  items = [];
  assert.equal(await context.toggleMine('XYZ'), true);
  assert.equal(context.mineRows().length, 0);
  const before = state.mine;
  context.post = async () => {throw Error('save failed');};
  assert.equal(await context.toggleMine('FPT'), false);
  assert.equal(state.mine, before); // failed writes leave the list unchanged
  console.log('mylist frontend: immediate rows, current session, privacy, add/remove and failure passed');
})().catch(err => { console.error(err); process.exitCode = 1; });
