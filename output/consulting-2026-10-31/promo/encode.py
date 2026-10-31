from pathlib import Path
import subprocess,concurrent.futures,json
root=Path(__file__).resolve().parent
(root/'clips').mkdir(exist_ok=True)
frames=sorted((root/'frames').glob('*.png'))
def encode(p):
 out=root/'clips'/f'{p.stem}.mp4'
 subprocess.run(['ffmpeg','-v','error','-y','-loop','1','-i',str(p),'-t','6','-vf','fade=t=in:st=0:d=0.25,fade=t=out:st=5.75:d=0.25,format=yuv420p','-r','24','-c:v','libx264','-preset','fast','-crf','19','-threads','2',str(out)],check=True)
 return out
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as e: list(e.map(encode,frames))
(root/'clips.txt').write_text('\n'.join("file '"+str(root/'clips'/f'{p.stem}.mp4')+"'" for p in frames))
subprocess.run(['ffmpeg','-v','error','-y','-f','concat','-safe','0','-i',str(root/'clips.txt'),'-c','copy','-movflags','+faststart',str(root/'voice-harness-admin-promo.mp4')],check=True)
# Compact teaser from four complete scenes: local workspace, caller customisation, evidence inspection, conclusion.
(root/'teaser.txt').write_text('\n'.join("file '"+str(root/'clips'/f'{i:02}.mp4')+"'" for i in [0,3,10,18]))
subprocess.run(['ffmpeg','-v','error','-y','-f','concat','-safe','0','-i',str(root/'teaser.txt'),'-vf','fps=8,scale=960:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer','-loop','0',str(root/'voice-harness-admin-teaser.gif')],check=True)
print('Encoded',len(frames)*6,'seconds')
