"""Build a self-contained, downloadable copy of the evidence site: python3 scripts/build_site_bundle.py [--lite]."""
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVE = '''"""Serve this folder and open the evidence hub: python3 serve.py [port]."""
import http.server
import socketserver
import sys
import webbrowser

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
handler = http.server.SimpleHTTPRequestHandler
with socketserver.TCPServer(('127.0.0.1', port), handler) as server:
    print(f'Serving on http://127.0.0.1:{port}/  (Ctrl+C to stop)')
    webbrowser.open(f'http://127.0.0.1:{port}/')
    server.serve_forever()
'''
README = '''Voice Harness evidence site{lite}

1. Open a terminal in this folder.
2. Run:  python3 serve.py
3. Your browser opens at http://127.0.0.1:8000/ (pages need HTTP; double-clicking a .html file will not work).

Change colours:  site/theme.css (one place, every page follows).
Work log:        work-logs/ (see work-logs/README.md). Copy it across when you download a newer bundle.
{note}'''

def run(script, *args):
    subprocess.run([sys.executable, str(ROOT / 'scripts' / script), *args], cwd=ROOT, check=True)  # noqa: S603

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--lite', action='store_true', help='leave out audio (WAV/FLAC); audio links will not play')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    name = 'voice-harness-site-lite' if args.lite else 'voice-harness-site'
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    zip_path = out / f'{name}.zip'
    run('build_test_setup.py')
    run('build_business_dashboard.py')
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / name
        run('build_report_catalogue.py', '--output', str(stage))
        shutil.copytree(ROOT / 'work-logs', stage / 'work-logs', ignore=shutil.ignore_patterns('__pycache__'))
        (stage / 'serve.py').write_text(SERVE)
        index = stage / 'index.html'
        marker = '<a href="#archive">Archive</a>'
        html = index.read_text()
        if marker not in html:
            raise SystemExit('index.html nav changed: update the work-log link in build_site_bundle.py')
        index.write_text(html.replace(marker, marker + '<a href="work-logs/index.html">Work log</a>', 1))
        if args.lite:
            for audio in [*stage.rglob('*.wav'), *stage.rglob('*.flac')]:
                audio.unlink()
        (stage / 'README.txt').write_text(README.format(
            lite=' (lite: no audio)' if args.lite else '',
            note='This is the lite bundle: audio files are removed, so listening pages will not play.\n' if args.lite else ''))
        zip_path.unlink(missing_ok=True)
        shutil.make_archive(str(zip_path.with_suffix('')), 'zip', root_dir=tmp, base_dir=name)
    print(f'{zip_path} ({zip_path.stat().st_size / 1e6:.0f} MB)')

if __name__ == '__main__':
    main()
