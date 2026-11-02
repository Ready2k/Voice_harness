from pathlib import Path
import subprocess, concurrent.futures
root=Path(__file__).resolve().parent
def enc(p):
 out=root/'clips'/f'{p.stem}.mp4'
 subprocess.run(['ffmpeg','-v','error','-y','-loop','1','-i',str(p),'-vf',"scale=7680:4320:flags=lanczos,zoompan=z='min(zoom+0.00035,1.031)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=90:s=1920x1080:fps=30,fade=t=in:st=0:d=0.08,format=yuv420p",'-t','3','-c:v','libx264','-preset','fast','-crf','19','-threads','2',str(out)],check=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(enc,sorted((root/'frames').glob('*.png'))))
for name,ids,length in [('voice-harness-reporting-short-24s',range(8),24)]:
 listing=root/(name+'.txt');listing.write_text('\n'.join("file '"+str(root/'clips'/f'{i:02}.mp4')+"'" for i in ids))
 subprocess.run(['ffmpeg','-v','error','-y','-f','concat','-safe','0','-i',str(listing),'-i',str(root.parents[1]/'product-spots' /'.build'/'pulse.wav'),'-t',str(length),'-c:v','copy','-c:a','aac','-b:a','192k','-af',f'afade=t=out:st={length-.7}:d=0.7','-movflags','+faststart',str(root.parent/(name+'.mp4'))],check=True)
print('Created 24-second reporting spot')
