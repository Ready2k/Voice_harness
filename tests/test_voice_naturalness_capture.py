"""The matched-script recorder must exercise GPT-Live and retain its capture configuration."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from voice_harness import live_providers

spec = importlib.util.spec_from_file_location('voice_naturalness', Path(__file__).parents[1] / 'scripts/voice_naturalness.py')
recorder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recorder)


def test_gpt_live_capture_records_provenance_and_uses_explicit_spoken_request(monkeypatch):
    class Adapter:
        closed = False

        async def open_session(self, config):
            self.config = config
            return {'model_id': 'gpt-live-1', 'backend_model_id': 'gpt-6-luna'}

        async def send_text(self, text):
            self.text = text

        async def receive(self):
            yield SimpleNamespace(type='audio_out', pcm=b'\x01\x00' * 24, sample_rate=24000)
            yield SimpleNamespace(type='transcript_final', role='assistant', text=recorder.LINES[0])
            yield SimpleNamespace(type='turn_end')

        async def close(self, reason):
            self.closed = True

    adapter = Adapter()
    monkeypatch.setattr(live_providers, '_adapter', lambda _: (adapter, {'model_id': 'gpt-live-1'}))
    metadata = {}
    pcm, said = asyncio.run(recorder.speak_once('openai-live', 'marin', recorder.LINES[0], metadata=metadata))
    assert len(pcm) == 48
    assert said == recorder.LINES[0]
    assert adapter.closed
    assert adapter.text.startswith('Repeat this aloud, word for word, and nothing else:')
    assert metadata['caller_text'] == adapter.text
    assert metadata['configuration']['voice'] == 'marin'
    assert metadata['configuration']['tools'] == []
    assert metadata['receipt']['model_id'] == 'gpt-live-1'
    assert metadata['provider_transcript'] == recorder.LINES[0]
    assert metadata['delivered_voice_instructions'].endswith(recorder.READ_ALOUD)


def test_render_refuses_to_overwrite_recordings(tmp_path, monkeypatch):
    (tmp_path / 'key.json').write_text('{}')
    monkeypatch.setattr(recorder, 'load_env', lambda: None)
    import pytest
    with pytest.raises(SystemExit, match='already contains'):
        recorder.render(tmp_path, ['openai-live'])
