"""Offline checks of provider-specific telephone transport semantics."""
import unittest
from unittest.mock import AsyncMock, patch

from voice_harness.telephone_providers import TelephoneAdapter, build_adapter, provider_options


class NovaDouble:
    def __init__(self):
        self.open_session = AsyncMock(return_value={'session_id': 'offline'})
        self.text = []
        self.results = []

    async def send_text(self, text):
        self.text.append(text)

    async def submit_tool_result(self, call_id, result):
        self.results.append((call_id, result))
        return {'call_id': call_id}


class TelephoneProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_nova_uses_delegate_prompt_and_does_not_trigger_on_connection_notice(self):
        native = NovaDouble()
        adapter = TelephoneAdapter(native, 'nova-sonic-live', {'AWS_REGION': 'offline'})
        with patch('voice_harness.telephone_providers._nova_env', return_value={'AWS_SESSION_TOKEN': 'offline'}):
            receipt = await adapter.open_session({'instructions': 'Delegate wallet brief', 'tools': [{'name': 'phone_respond'}]})
        config = native.open_session.await_args.args[0]
        self.assertEqual(config['system_prompt'], 'Delegate wallet brief')
        self.assertEqual(config['tools'], [{'name': 'phone_respond'}])
        self.assertEqual(config['env']['AWS_SESSION_TOKEN'], 'offline')
        self.assertEqual(receipt['provider_config_id'], 'nova-sonic-live')
        await adapter.send_text('connected', respond=False)
        await adapter.send_text('PROMPT_EVENT_ID=p1', respond=True)
        self.assertEqual(native.text, ['PROMPT_EVENT_ID=p1'])
        self.assertEqual(await adapter.submit_tool_result('call1', {'call_id': 'call1'}, respond=False), {'call_id': 'call1'})

    async def test_gemini_uses_its_vad_and_keeps_context_only_text(self):
        class GeminiDouble(NovaDouble):
            async def send_text(self, text, *, respond=True):
                self.text.append((text, respond))
        native = GeminiDouble()
        adapter = TelephoneAdapter(native, 'gemini-live', {})
        await adapter.open_session({'instructions': 'Delegate', 'turn_detection': {'create_response': False}})
        config = native.open_session.await_args.args[0]
        self.assertEqual(config['turn_detection'], 'server_vad')
        self.assertEqual(config['model_id'], 'gemini-3.8-live')
        await adapter.send_text('connected', respond=False)
        self.assertEqual(native.text, [('connected', False)])
        await adapter.submit_tool_result('call1', {'call_id': 'call1'}, respond=True)
        self.assertEqual(len(native.results), 1)

    def test_all_choices_build_correct_adapter_without_fallback(self):
        for provider, class_name in [('openai-live', 'OpenAILiveAdapter'), ('openai-realtime-live', 'OpenAIRealtimeAdapter'), ('gemini-live', 'GeminiLiveAdapter'),
                                     ('nova-sonic-live', 'NovaSonicAdapter'), ('xai-voice-live', 'XaiVoiceAdapter'),
                                     ('elevenlabs-live', 'ElevenLabsVoiceAdapter')]:
            with self.subTest(provider=provider):
                self.assertEqual(type(build_adapter(provider, {}).native).__name__, class_name)
        with self.assertRaises(ValueError):
            build_adapter('unknown', {})

    def test_provider_options_report_missing_setup(self):
        with patch('voice_harness.telephone_providers.requirements', return_value=['NO_CONFIG']):
            options = provider_options({})
        self.assertEqual(len(options), 6)
        self.assertTrue(all(p['missing'] == ['NO_CONFIG'] for p in options))
