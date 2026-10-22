import {el} from './runs.mjs?v=ux-final-20261025';

export function balanceView(item, now = Date.now() / 1000) {
  const spend = item.kind === 'spend';
  const live = item.status === 'live' && Number.isFinite(item.balance);
  const stale = live && (!Number.isFinite(item.checked_at) || now - item.checked_at > (item.stale_after_seconds ?? 300));
  const manual = !live && Number.isFinite(item.manual_balance);
  const value = live ? item.balance : manual ? item.manual_balance : null;
  const currency = live ? item.currency : item.manual_currency;
  const comparable = value !== null && currency === item.threshold_currency;
  const low = comparable && (spend ? value >= item.threshold : value <= item.threshold);
  const empty = !spend && value !== null && value <= 0;
  const mismatch = value !== null && !comparable;
  return {
    value, currency, live, manual, stale,
    tone: empty ? 'bad' : low || !live || stale || mismatch ? 'warn' : 'ok',
    label: stale ? 'Reading out of date' : manual ? (empty ? 'Saved balance exhausted' : low ? 'Saved balance low' : 'Saved balance · not live')
      : !live ? (item.status === 'setup_required' ? 'Setup needed' : spend ? 'Spend unavailable' : 'Balance unavailable')
      : spend ? (low ? 'Monthly spend alert reached' : 'Below monthly spend alert')
      : empty ? 'Prepaid credit exhausted' : low ? 'Low balance' : mismatch ? 'Alert currency mismatch' : 'Above alert threshold',
    source: live ? (spend ? 'Estimated month-to-date spend' : 'Provider credit reading') : manual ? 'Manually recorded · may have changed' : 'No balance available',
    observedAt: live ? item.checked_at : item.recorded_at,
    attention: !live || stale || low || empty || mismatch,
    low: low || empty,
  };
}

export function money(value, currency) {
  if (!Number.isFinite(value)) return 'Unavailable';
  try { return new Intl.NumberFormat(undefined, {style:'currency', currency, currencyDisplay:'code'}).format(value); }
  catch { return `${value.toFixed(2)} ${currency}`; }
}

export function balanceSummary(items, now) {
  const views = items.map(item => balanceView(item, now));
  return {live:views.filter(v => v.live && !v.stale).length, low:views.filter(v => v.low).length,
    attention:views.filter(v => v.attention).length};
}

function field(form, title, type, value) {
  const label = el('label', '', title);
  const input = el('input'); input.type = type; input.value = value ?? '';
  label.append(input); form.append(label); return input;
}

export function renderBalanceCard(item, save) {
  const card = el('section', 'panel balance-card');
  const head = el('div', 'balance-head'); head.append(el('h2', '', item.name));
  const status = el('span', 'chip'); head.append(status);
  const scope = el('p', 'muted', item.scope);
  const value = el('p', 'balance-value');
  const source = el('p', 'muted');
  const stamp = el('p', 'muted');
  const message = el('p', 'balance-message');
  const billing = el('a', '', 'Open provider billing ↗'); billing.href = item.billing_url; billing.target = '_blank'; billing.rel = 'noopener noreferrer';
  const details = el('details', 'balance-settings'); details.append(el('summary', '', 'Alert threshold'+(item.kind === 'spend' ? '' : ' & saved balance')));
  const form = el('form');
  const threshold = field(form, item.kind === 'spend' ? 'Warn at or above' : 'Warn at or below', 'number', item.threshold); threshold.min='0'; threshold.max='1000000000000'; threshold.step='0.01'; threshold.required=true;
  const currencyLabel=el('label','','Currency'); const currency=el('select');
  for(const code of ['USD','GBP','EUR']){const option=el('option','',code);option.value=code;currency.append(option);} currency.value=item.threshold_currency;
  currencyLabel.append(currency); form.append(currencyLabel);
  let manual=null, record=null;
  if(item.kind !== 'spend'){
    manual=field(form, 'Balance from billing (optional)', 'number', item.manual_balance); manual.step='0.01'; manual.min='-1000000000000';manual.max='1000000000000';
    const recordLabel=el('label','balance-check'); record=el('input');record.type='checkbox';recordLabel.append(record,el('span','','Record this balance as checked now (blank clears it)'));form.append(recordLabel);
    manual.addEventListener('input',()=>{record.checked=true;});
    form.append(el('p','muted','Saved balances never update automatically or override a live reading. Use the currency shown by your provider.'));
  } else if(item.id === 'aws') {
    form.append(el('p','muted','Cost Explorer reports estimated charges and may lag more than a day. AWS charges $0.01 per paginated query; this reading is cached for 24 hours.'));
  } else {
    form.append(el('p','muted','Google Cloud reports usage through Cloud Billing export. Export and BigQuery data may lag; BigQuery storage and queries can incur charges.'));
  }
  const submit=el('button','','Save settings');submit.type='submit'; const feedback=el('p','muted');feedback.setAttribute('role','status');form.append(submit,feedback);
  form.addEventListener('submit',async event=>{
    event.preventDefault();submit.disabled=true;feedback.textContent='Saving…';
    try{await save(item.id,{threshold:Number(threshold.value),currency:currency.value,manual_balance:manual?.value===''||!manual?null:Number(manual.value),record_balance:record?.checked??false});if(record)record.checked=false;feedback.textContent='Saved.';}
    catch(e){feedback.textContent='Could not save: '+e.message;}finally{submit.disabled=false;}
  });
  details.append(form);card.append(head,scope,value,source,stamp,message,billing,details);
  return {card, update(next){
    const view=balanceView(next);card.dataset.tone=view.tone;status.textContent=view.label;
    value.textContent=money(view.value,view.currency);source.textContent=view.source;
    stamp.textContent=view.observedAt ? (view.live && next.kind === 'spend' ? `Checked ${new Date(view.observedAt*1000).toLocaleString()} · billing period from ${next.period_start ?? 'this month'}` : 'Observed '+new Date(view.observedAt*1000).toLocaleString()) : 'No balance observation yet';
    message.textContent=next.message;
    if(view.value!==null && view.currency!==next.threshold_currency)message.textContent+=' Alert currency differs from this balance; update the threshold currency.';
  }};
}
