import test from 'node:test';
import assert from 'node:assert/strict';
import {launchBody, providerChoices, transcript} from '../../apps/web/runs.mjs';
import {comparisonCheck} from '../../apps/web/compare.mjs';
import {pcmWav} from '../../apps/web/caller-recordings.mjs';

test('caller selection is independent of target and omitted for fixtures',()=>{
 const caller={mode:'live_ai',persona:'anxious',voice:'marin',max_turns:6,max_duration_seconds:120};
 const body=launchBody({id:'happy-balance-clean-001',version:1,live:true},'strict-v1','xai-voice-live',caller);
 assert.equal(body.provider_config_id,'xai-voice-live');assert.deepEqual(body.caller,caller);
 assert.equal(launchBody({id:'mock',version:1},'strict-v1',null,caller).caller,undefined);
});
test('live caller readiness does not require a system synthesiser',()=>{
 const payload={items:[{provider_config_id:'openai-realtime-live',available:false,reason:'NO_CALLER_VOICE_SYNTH',
   caller_modes:{live_ai:{available:true,reasons:[]}},fixed_backends:{openai_tts:{available:true,reasons:[]}}}]};
 assert.equal(providerChoices(payload)[0].disabled,true);
 assert.equal(providerChoices(payload,{mode:'live_ai'})[0].disabled,false);
 assert.equal(providerChoices(payload,{fixed_backend:'openai_tts'})[0].disabled,false);
});
test('generated caller transcript replaces the fixed-script placeholder',()=>{
 const lines=transcript([{sequence:1,type:'caller.utterance',content:{text:'Could you clarify?'}},
   {sequence:2,type:'input.caller_audio',content:{turn:0}}]);
 assert.equal(lines.length,1);assert.equal(lines[0].text,'Could you clarify?');
 assert.match(lines[0].note,/provider transcript/);
});
test('comparisons disclose live caller and persona differences',()=>{
 const config={scenario_id:'balance',scenario_version:1};
 const run=(id,caller)=>({run_id:id,config:{...config,caller}});
 const check=comparisonCheck([run('x',{mode:'fixed'}),run('y',{mode:'live_ai',persona:'calm'})]);
 assert.ok(check.differences.includes('caller'));assert.ok(check.adaptiveCaller);
 assert.ok(comparisonCheck([run('x',{mode:'live_ai',persona:'calm'}),run('y',{mode:'live_ai',persona:'anxious'})]).differences.includes('caller'));
});
test('recorded PCM is wrapped in a mono 24kHz WAV without altering samples',async()=>{
 const blob=pcmWav('01000200');const bytes=await blob.arrayBuffer();const view=new DataView(bytes);
 assert.equal(blob.type,'audio/wav');assert.equal(view.getUint32(24,true),24000);
 assert.equal(view.getUint32(40,true),4);assert.equal(view.getInt16(44,true),1);assert.equal(view.getInt16(46,true),2);
});
