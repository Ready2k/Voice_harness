import test from 'node:test';
import assert from 'node:assert/strict';
import {balanceView, balanceSummary, money} from '../../apps/web/balances.mjs';
const reading = {status:'live',balance:20,currency:'USD',checked_at:1000,threshold:10,threshold_currency:'USD'};
test('zero and debt warn; missing does not turn into zero',()=>{
  assert.equal(balanceView({...reading,balance:0},1001).label,'Prepaid credit exhausted');
  assert.equal(balanceView({...reading,balance:-2},1001).tone,'bad');
  const missing=balanceView({...reading,status:'unavailable',balance:null},1001);
  assert.equal(missing.value,null);assert.equal(missing.attention,true);assert.equal(money(null,'USD'),'Unavailable');
});
test('low threshold is inclusive; currencies are not mixed',()=>{
  assert.equal(balanceView({...reading,balance:10},1001).label,'Low balance');
  assert.equal(balanceView({...reading,balance:10,threshold_currency:'GBP'},1001).label,'Alert currency mismatch');
});
test('failed, manual and stale readings can never look healthy',()=>{
  assert.equal(balanceView(reading,1400).stale,true);
  const manual=balanceView({...reading,status:'unavailable',balance:null,manual_balance:100,manual_currency:'USD',recorded_at:800},1001);
  assert.equal(manual.live,false);assert.equal(manual.tone,'warn');assert.equal(manual.observedAt,800);
  assert.equal(balanceSummary([reading,{...reading,status:'unsupported',balance:null}],1001).live,1);
});
