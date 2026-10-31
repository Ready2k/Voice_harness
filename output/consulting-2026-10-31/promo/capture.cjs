const {chromium}=require('/Users/jamescregeen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('fs');
const out='output/consulting-2026-10-31/promo/';
(async()=>{const b=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});const p=await b.newPage({viewport:{width:1600,height:1000}});const token=fs.readFileSync('.local/host-data/operator-token','utf8').trim();await p.addInitScript(t=>sessionStorage.setItem('harness-operator-token',t),token);
for(const name of ['runs','scenarios','bakeoff','manual','review','coverage','compare','telephone']){await p.goto('http://127.0.0.1:8766/client/'+name+'.html');await p.waitForTimeout(1800);fs.writeFileSync(out+name+'.txt',await p.locator('body').innerText());await p.screenshot({path:out+'screens/'+name+'.png'});console.log(name+' captured');if(name==='runs'){await p.getByRole('button',{name:'+ New evaluation'}).click();await p.waitForTimeout(500);fs.writeFileSync(out+'new-evaluation.txt',await p.locator('body').innerText());await p.screenshot({path:out+'screens/new-evaluation.png'});}}
await b.close();process.exit(0)})();
