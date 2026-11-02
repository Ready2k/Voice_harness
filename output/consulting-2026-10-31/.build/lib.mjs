import fs from 'node:fs/promises';
import {Presentation,PresentationFile} from '@oai/artifact-tool';
export const ROOT='/Users/jamescregeen/Code_Projects/Voice_Harness';
export const DIR=ROOT+'/output/consulting-2026-10-31';
export const SKILL='/Users/jamescregeen/.codex/plugins/cache/openai-primary-runtime/presentations/26.1025.11240/skills/presentations';
export const C={ink:'#102D3A',teal:'#007F78',muted:'#526773',light:'#ECF3F3',white:'#FFFFFF',amber:'#966116',red:'#9B2C2C'};
export const local=(s)=>ROOT+'/'+s;
export const rd=async(f)=>JSON.parse(await fs.readFile(ROOT+'/'+f,'utf8'));
export function kit(footer){
  const p=Presentation.create({slideSize:{width:1280,height:720}});
  const tables=[];
  function text(s,t,x,y,w,h,size=26,color=C.ink,bold=false){const a=s.shapes.add({geometry:'textbox',position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:'none',width:0}});a.text=t;a.text.style={typeface:'Arial',fontSize:size,color,bold};return a;}
  function slide(title,sub='',sources=[],dark=false){let s=p.slides.add();s.background.fill=dark?C.ink:C.white;let n=p.slides.items.length;text(s,'VOICE HARNESS  /  PROTOTYPE',56,28,1050,26,15,dark?'#79D4CA':C.teal,true);text(s,title,56,82,1168,112,42,dark?C.white:C.ink,true);if(sub)text(s,sub,56,185,1168,64,23,dark?'#D7E6E8':C.muted);text(s,footer,56,679,1110,23,14,dark?'#B6CBCD':C.muted);text(s,String(n).padStart(2,'0'),1190,679,40,23,14,dark?'#B6CBCD':C.muted);s.speakerNotes.text='Sources and interpretation\n'+sources.join('\n')+'\nThese observations are about specific prototype configurations on specific days. They do not certify any model or deployment.';return s;}
  function table(s,headers,rows,widths,y=265,height=335,size=23){tables.push(p.slides.items.length);let values=[headers,...rows];let t=s.tables.add({rows:values.length,columns:headers.length,left:56,top:y,width:1168,height,values,columnWidths:widths});t.borders.assign({fill:'#D6E2E3',width:0.6});t.cells.block({row:0,column:0,rowCount:values.length,columnCount:headers.length}).assign({textStyle:{typeface:'Arial',fontSize:size,color:C.ink},margins:{left:12,right:12,top:4,bottom:4}});for(let r=0;r<values.length;r++){t.rows[r].height=r===0?44:(height-44)/(values.length-1);for(let c=0;c<headers.length;c++){let cell=t.getCell(r,c);cell.fill=r===0?C.ink:(r%2?C.light:C.white);if(r===0)cell.text.style={typeface:'Arial',fontSize:size,bold:true,color:C.white};}}return t;}
  function pair(s,lt,lb,rt,rb){text(s,lt,56,284,535,50,30,C.teal,true);text(s,lb,56,349,535,250,27);text(s,rt,665,284,559,50,30,C.teal,true);text(s,rb,665,349,559,250,27);}
  function foot(s,t,y=620){text(s,t,56,y,1168,47,18,C.muted);}
  function tile(s,x,y,w,h,big,label,color=C.teal){let b=s.shapes.add({geometry:'rect',position:{left:x,top:y,width:w,height:h},fill:C.light,line:{fill:'none',width:0}});text(s,big,x+16,y+10,w-32,70,52,color,true);text(s,label,x+16,y+86,w-32,h-92,20,C.ink);return b;}
  async function finish(name,receipt){
    await fs.mkdir(DIR+'/.build/renders-'+name,{recursive:true});
    await (await PresentationFile.exportPptx(p)).save(DIR+'/.build/candidate-'+name+'.pptx');
    const {finalizePresentation}=await import(SKILL+'/container_tools/artifact_tool_utils.mjs');
    const nums=[...new Set(tables)];
    await finalizePresentation({workspaceDir:DIR,candidatePath:DIR+'/.build/candidate-'+name+'.pptx',finalPath:DIR+'/deliverables/'+name+'.pptx',pythonExecutable:'/Users/jamescregeen/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3',integrityValidatorPath:SKILL+'/container_tools/inspect_presentation_package_integrity.py',layoutValidatorPath:SKILL+'/container_tools/inspect_presentation_layout_geometry.py',layoutArgs:['--expected-slide-size-emu','12192000,6858000','--validate-bullet-geometry','--validate-heading-fit',...nums.flatMap(n=>['--require-native-table-slide',String(n)])],fontPolicy:{basis:'design',families:['Arial']},requiredNativeTableOwnerSlides:nums,verifyArtifactToolImport:true,receiptPath:DIR+'/.build/'+receipt});
    for(let i=0;i<p.slides.items.length;i++){let png=await p.export({slide:p.slides.items[i],format:'png',scale:1});await fs.writeFile(DIR+'/.build/renders-'+name+'/slide-'+String(i+1).padStart(2,'0')+'.png',new Uint8Array(await png.arrayBuffer()));}
    console.log('Completed '+p.slides.items.length+' slides');
  }
  return {p,text,slide,table,pair,foot,tile,finish};
}
