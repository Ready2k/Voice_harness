"""Create a dated work-log entry and add it to logs.json: python3 work-logs/new_log.py "Title"."""
import datetime
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

def main():
    title = ' '.join(sys.argv[1:]).strip() or 'Untitled'
    today = datetime.date.today().isoformat()
    name = f"{today}-{re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-') or 'entry'}.md"
    path = HERE / name
    if path.exists():
        raise SystemExit(f'{name} already exists')
    path.write_text(f'# {title}\n\n{today}\n\n## What I showed\n\n- \n\n## Questions raised\n\n- \n\n## Follow-ups\n\n- \n')
    index = HERE / 'logs.json'
    logs = json.loads(index.read_text()) if index.exists() else []
    logs.insert(0, dict(file=name, title=title, date=today))
    index.write_text(json.dumps(logs, indent=2) + '\n')
    print(f'Created {path}')

if __name__ == '__main__':
    main()
