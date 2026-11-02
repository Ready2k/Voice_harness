from pathlib import Path
import subprocess, concurrent.futures, wave
import numpy as np
root=Path(__file__).resolve().parent
sr=48000; dur=30; audio=np.zeros(sr*dur); rng=np.random.default_rng(42)
def add(sig,start):
 i=int(start*sr); n=min(len(sig),len(audio)-i)
 if n>0: audio[i:i+n]+=sig[:n]
for beat in range(60):
 start=beat*.5
 t=np.arange(int(.32*sr))/sr
 kick=np.sin(2*np.pi*(45*t+60*.045*(1-np.exp(-t/.045))))*np.exp(-t*16)
 add(kick*.7,start)
 if beat%2:
  t=np.arange(int(.17*sr))/sr; noise=rng.normal(0,1,len(t));noise=np.r_[0,np.diff(noise)]
  add(noise*np.exp(-t*32)*.11,start)
 for half in range(2):
  t=np.arange(int(.065*sr))/sr;noise=rng.normal(0,1,len(t));noise=np.r_[0,np.diff(noise)]
  add(noise*np.exp(-t*80)*.035,start+half*.25)
 f=[65.406,77.782,87.307,58.270][(beat//8)%4]
 t=np.arange(int(.40*sr))/sr
 add((np.sin(2*np.pi*f*t)+.18*np.sin(2*np.pi*2*f*t))*np.minimum(t/.012,1)*np.exp(-t*6)*.25,start)
 for half in range(2):
  f2=f*([4,6,8,6][(beat*2+half)%4]);t=np.arange(int(.22*sr))/sr
  add(np.sin(2*np.pi*f2*t)*np.minimum(t/.006,1)*np.exp(-t*18)*.12,start+half*.25)
audio=np.tanh(audio);audio*=.7/max(abs(audio));audio*=np.minimum(np.arange(len(audio))/sr/.15,1);audio*=np.minimum((len(audio)-np.arange(len(audio)))/sr/1,1)
with wave.open(str(root/'pulse.wav'),'wb') as w:
 w.setnchannels(1);w.setsampwidth(2);w.setframerate(sr);w.writeframes((audio*32767).astype('<i2').tobytes())
def enc(p):
 out=root/'clips'/f'{p.stem}.mp4'
 subprocess.run(['ffmpeg','-v','error','-y','-loop','1','-i',str(p),'-vf',"scale=7680:4320:flags=lanczos,zoompan=z='min(zoom+0.00035,1.031)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=90:s=1920x1080:fps=30,fade=t=in:st=0:d=0.08,format=yuv420p",'-t','3','-c:v','libx264','-preset','fast','-crf','19','-threads','2',str(out)],check=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(enc,sorted((root/'frames').glob('*.png'))))
for name,ids,length in [('voice-harness-product-spot-30s',range(10),30),('voice-harness-product-spot-15s',[0,2,5,6,9],15)]:
 listing=root/(name+'.txt');listing.write_text('\n'.join("file '"+str(root/'clips'/f'{i:02}.mp4')+"'" for i in ids))
 subprocess.run(['ffmpeg','-v','error','-y','-f','concat','-safe','0','-i',str(listing),'-i',str(root/'pulse.wav'),'-t',str(length),'-c:v','copy','-c:a','aac','-b:a','192k','-af',f'afade=t=out:st={length-.7}:d=0.7','-movflags','+faststart',str(root.parent/(name+'.mp4'))],check=True)
print('Created 30s and 15s spots with original synth soundtrack')
