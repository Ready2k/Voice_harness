"""Live protocol regression tests against an offline WebSocket endpoint."""
import asyncio
import base64
import json
import unittest

import websockets

from voice_harness.adapters import CapabilityUnsupported
from voice_harness.adapters.openai_live import OpenAILiveAdapter
from voice_harness.live_providers import _adapter, capability_binding, configure_transport


class LiveAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_audio_tool_continuation_and_finalization(self):
        received = []
        async def server(ws):
            self.assertIsNone(ws.request.path.partition('?')[2] or None)
            async for raw in ws:
                event = json.loads(raw)
                received.append(event)
                if event['type'] == 'session.start':
                    await ws.send(json.dumps({'type': 'session.started', 'session': {'id': 'live-test', 'model': 'gpt-live-1'}}))
                if event['type'] == 'session.close':
                    await ws.send(json.dumps({'type': 'session.closed', 'usage': {'seconds': 2}}))
        async with websockets.serve(server, '127.0.0.1', 0) as endpoint:
            adapter = OpenAILiveAdapter(api_key='offline', url=f'ws://127.0.0.1:{endpoint.sockets[0].getsockname()[1]}')
            try:
                receipt = await adapter.open_session({'session_id': 'test', 'tools': [{'name': 'verify_identity', 'parameters': {'type': 'object'}}]})
                self.assertEqual(receipt['provider_session_id'], 'live-test')
                await adapter.send_audio(bytes(960), {'sample_count': 480})
                await adapter.request_turn()
                adapter._handle({'type': 'response.event', 'event': {'type': 'response.function_call_arguments.done', 'call_id': 'ignored'}})
                self.assertFalse(adapter._pending_calls)
                adapter._handle({'type': 'response.event', 'event': {'type': 'response.output_item.done', 'item': {
                    'type': 'function_call', 'call_id': 'c1', 'name': 'verify_identity', 'arguments': '{}'}}})
                with self.assertRaisesRegex(ValueError, 'CORRELATION'):
                    await adapter.submit_tool_result('wrong', {'call_id': 'wrong'})
                result = await adapter.submit_tool_result('c1', {'call_id': 'c1', 'status': 'ok'})
                self.assertEqual(result['provider_acknowledgement'], 'unconfirmed')
                adapter._handle({'type': 'response.event', 'event': {'type': 'response.output_item.done', 'item': {
                    'type': 'function_call', 'call_id': 'c1', 'name': 'verify_identity', 'arguments': '{}'}}})
                self.assertFalse(adapter._pending_calls)
                await adapter.send_text('The orchestrator confirmation ID is confirmation_123.', respond=False)
                with self.assertRaises(CapabilityUnsupported):
                    await adapter.interrupt(None, 0)
            finally:
                await adapter.close('test')
            self.assertTrue(adapter._finalized)
        kinds = [e['type'] for e in received]
        self.assertEqual(kinds[0], 'session.start')
        self.assertIn('session.input_audio.append', kinds)
        self.assertIn('response.item.create', kinds)
        self.assertIn('response.create', kinds)
        self.assertNotIn('input_audio_buffer.commit', kinds)
        update = next(e for e in received if e['type'] == 'session.update')
        self.assertEqual(update['session']['delegation']['type'], 'responses')
        self.assertEqual(update['session']['delegation']['responses']['model'], 'gpt-6-luna')
        self.assertIn('confirmation_123', update['session']['delegation']['responses']['instructions'])
        self.assertIn('session.instructions.append', kinds)
        self.assertEqual(received[0]['session']['delegation']['responses']['tools'][0]['type'], 'function')

    async def test_pcm_silence_ends_speech_but_backend_completion_does_not(self):
        a = OpenAILiveAdapter(api_key='offline')
        native_events = []
        a.native_sink = lambda adapter, kind, payload: native_events.append(kind)
        a._handle({'type': 'session.output_audio.delta', 'delta': base64.b64encode(b'\xff\x0f' * 480).decode()})
        a._handle({'type': 'session.output_transcript.delta', 'delta': 'Hello'})
        a._handle({'type': 'response.event', 'event': {'type': 'response.completed', 'response': {}}})
        self.assertIsNotNone(a._response_id)
        self.assertEqual(native_events, ['harness.pcm_speech_detected'])
        silence = {'type': 'session.output_audio.delta', 'delta': base64.b64encode(bytes(24000 * 2)).decode()}
        a._handle(silence)
        self.assertIsNotNone(a._response_id)  # acknowledgement silence cannot end a pending result
        from unittest.mock import patch
        with patch('voice_harness.adapters.openai_live.time.monotonic', return_value=a._reply_not_before + 1):
            a._handle(silence)
        events = []
        while not a._queue.empty():
            events.append(a._queue.get_nowait())
        self.assertEqual(native_events, ['harness.pcm_speech_detected'])
        from voice_harness.bakeoff import _is_audio
        self.assertTrue(_is_audio('openai-live', {'native_type': native_events[0]}))
        self.assertFalse(_is_audio('openai-live', {'native_type': 'session.output_audio.delta'}))
        self.assertEqual([e.type for e in events].count('turn_end'), 1)
        self.assertEqual(next(e.text for e in events if e.type == 'transcript_final'), 'Hello')

    async def test_acknowledgement_cannot_finish_while_caller_is_speaking(self):
        from unittest.mock import patch
        a = OpenAILiveAdapter(api_key='offline')
        a._handle({'type': 'session.output_audio.delta', 'delta': base64.b64encode(b'\xff\x0f' * 480).decode()})
        with patch('voice_harness.adapters.openai_live.time.monotonic', return_value=100):
            await a.send_audio(b'\xff\x0f' * 480, {'sample_count': 480})
            a._handle({'type': 'session.output_audio.delta', 'delta': base64.b64encode(bytes(24000 * 2)).decode()})
        self.assertIsNotNone(a._response_id)
        self.assertEqual(a._reply_not_before, 103)

    async def test_registry_and_transport_are_distinct_from_realtime(self):
        a, config = _adapter('openai-live', 'advanced-v1')
        self.assertIsInstance(a, OpenAILiveAdapter)
        transport = configure_transport('openai-live', config, 'fixed')
        self.assertEqual(transport['turn_commit'], 'provider_vad')
        self.assertFalse(transport['client_truncation'])
        self.assertEqual(capability_binding('openai-live')['capability_status'], 'diagnostic_unverified')

    async def test_startup_error_is_prompt_and_cleans_up(self):
        async def server(ws):
            await ws.recv()
            await ws.send(json.dumps({'type': 'error', 'error': {'code': 'invalid_value'}}))
            await ws.wait_closed()
        async with websockets.serve(server, '127.0.0.1', 0) as endpoint:
            a = OpenAILiveAdapter(api_key='offline', url=f'ws://127.0.0.1:{endpoint.sockets[0].getsockname()[1]}')
            with self.assertRaisesRegex(ValueError, 'REJECTED'):
                await asyncio.wait_for(a.open_session({}), 2)
            self.assertTrue(a.closed)
