import {viewChanged} from './ux-insights.mjs';
import {el} from './runs.mjs?v=ux-final-20261025';
import {words} from './workflow.mjs?v=ux-final-20261025';
export function renderCost(root,cost){
 if(!viewChanged(root,cost))return;
 const wasOpen=root.querySelector('details')?.open??false;
 const scroll=root.scrollTop;
 root.replaceChildren(el('h3','','Cost & metering'));
 if(cost.unavailable_reason){root.append(el('p','muted','Cost data is unavailable: '+cost.unavailable_reason));return;}
 root.append(el('p','', 'Completeness: '+words(cost.completeness)+' · Unknown lines: '+(cost.unknown_line_count??'unknown')),el('p','muted',cost.statement??'Amounts retain their currency and provenance. A partial subtotal is not a total cost.'));
 for(const s of cost.summaries??[]){const box=el('div','cost-currency');box.append(el('strong','',s.currency+' '+(s.total_cost??s.known_subtotal)+' · '+(s.total_cost==null?'known subtotal':'total')));for(const [p,v] of Object.entries(s.by_provenance??{}))box.append(el('p','muted',words(p)+': '+v));root.append(box);}
 if(!cost.summaries?.length)root.append(el('p','muted','No known monetary amount has been recorded. This does not mean the run was free.'));
 if(cost.unknown_components?.length)root.append(el('p','muted','Unknown components: '+cost.unknown_components.join(', ')));
 const details=el('details');details.append(el('summary','','Inspect cost lines & provenance'));const table=el('table');const head=el('tr');for(const h of ['Component','Provider / model','Provenance','Quantity','Amount','Unknown reason'])head.append(el('th','',h));table.append(head);
 for(const line of cost.lines??[]){if(line.superseded_by)continue;const row=el('tr');for(const text of [line.component,(line.provider??'Unknown')+' / '+(line.model??'Unknown'),words(line.provenance),line.quantity==null?'Unknown':line.quantity+' '+(line.unit??''),line.amount==null?'Unknown':line.currency+' '+line.amount,line.unknown_reason??'—'])row.append(el('td','',text));table.append(row);}details.open=wasOpen;details.append(table);root.append(details);root.scrollTop=scroll;
}
