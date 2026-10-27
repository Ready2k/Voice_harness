"""Opt-in paid GPT-Live audio/tool smoke. Prints event counts, never audio or keys.

PYTHONPATH=src .venv/bin/python scripts/probe/openai_live_smoke.py
"""
import asyncio
import json
import os
from collections import Counter
from pathlib import Path

from voice_harness.adapters.openai_live import OpenAILiveAdapter


async def main():
    for line in Path('.env').read_text().splitlines():
        if '=' in line and not line.strip().startswith('#'):
            key, value = line.removeprefix('export ').split('=', 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    adapter = OpenAILiveAdapter()
    counts = Counter()
    native = Counter()
    def native_event(_, kind, event):
        native.update([kind])
        if kind == "error":
            print(json.dumps({"provider_error": event.get("error", {})}))
    adapter.native_sink = native_event
    try:
        await adapter.open_session({'session_id': 'live-smoke', 'instructions':
            'When asked to check the test status, call check_status, then state the result briefly.',
            'tools': [{'name': 'check_status', 'description': 'Check the synthetic test status',
                       'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False}}]})
        await adapter.send_text('The synthetic test has no side effects.', respond=False)
        await adapter.send_text('Please check the test status.')
        async def consume():
            async for event in adapter.receive():
                counts[event.type] += 1
                if event.type == 'tool_call':
                    await adapter.submit_tool_result(event.call_id, {'call_id': event.call_id, 'status': 'ok',
                                                                    'result': 'The test status is green.'})
                if event.type == 'error':
                    print(json.dumps({'error_code': event.code, 'fatal': event.fatal}))
                if event.type == 'turn_end' and counts['tool_call'] and counts['audio_out']:
                    return
        await asyncio.wait_for(consume(), 40)
    finally:
        await adapter.close('smoke_done')
        print(json.dumps({'events': dict(counts), 'native_events': dict(native), 'finalized': adapter._finalized}))
    if counts['error'] or not adapter._finalized or not counts['tool_call'] or not counts['audio_out']:
        raise RuntimeError('Live smoke failed: inspect event counts above')


if __name__ == '__main__':
    asyncio.run(main())
