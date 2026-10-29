"""Live protocol regression tests against an offline WebSocket endpoint."""
import asyncio
import base64
import hashlib
import json
import time
import unittest
from unittest.mock import patch

import websockets

from voice_harness import audio_activity, bakeoff, bakeoff_disclosure
from voice_harness.adapters import CapabilityUnsupported
from voice_harness.adapters import openai_live as live
from voice_harness.adapters.openai_live import OpenAILiveAdapter
from voice_harness.judges.backends import JudgeConfig
from voice_harness.judges.events import TestedModel
from voice_harness.judges.independence import assess, is_tested_model, tested_identity
from voice_harness.live_providers import (
    TESTED_MODELS,
    _adapter,
    capability_binding,
    configure_transport,
    session_metadata,
)
from voice_harness.target_prompts import LIVE_DELEGATION_WRAPPER, STANDARDISED_V2

LOUD, QUIET = b'\xff\x0f' * 2400, bytes(4800)  # 100 ms each at 24 kHz


def delta(pcm):
    return {'type': 'session.output_audio.delta', 'delta': base64.b64encode(pcm).decode()}


def backend(kind):
    return {'type': 'response.event', 'event': {'type': kind, 'response': {}}}


class Clock:
    """A settable time.monotonic for the boundary rules."""
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def drain(adapter):
    out = []
    while not adapter._queue.empty():
        out.append(adapter._queue.get_nowait())
    return out


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
            natives = []
            adapter.native_sink = lambda name, kind, payload: natives.append(kind)
            try:
                receipt = await adapter.open_session({'session_id': 'test', 'instructions': STANDARDISED_V2,
                                                      'tools': [{'name': 'verify_identity', 'parameters': {'type': 'object'}}]})
                self.assertEqual(receipt['provider_session_id'], 'live-test')
                await adapter.send_audio(bytes(960), {'sample_count': 480})
                await adapter.request_turn()
                self.assertEqual(natives[-1], 'harness.caller_audio_flushed')
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
        # The recorded provenance is exactly what each model received, wrapper included.
        bound = session_metadata('openai-live', 'gpt-live-1', receipt)['target_prompt']['delivered']['models']
        sent = received[0]['session']
        self.assertEqual([m['role'] for m in bound], ['voice', 'backend'])
        self.assertEqual(bound[0]['instructions_sha256'], hashlib.sha256(sent['instructions'].encode()).hexdigest())
        self.assertEqual(bound[1]['instructions_sha256'],
                         hashlib.sha256(sent['delegation']['responses']['instructions'].encode()).hexdigest())
        self.assertEqual(sent['instructions'], LIVE_DELEGATION_WRAPPER + STANDARDISED_V2)
        self.assertEqual(bound[1]['model_id'], 'gpt-6-luna')

    async def test_pcm_silence_ends_speech_but_backend_completion_does_not(self):
        a, clock = OpenAILiveAdapter(api_key='offline'), Clock()
        native_events = []
        a.native_sink = lambda adapter, kind, payload: native_events.append(kind)
        with patch.object(live.time, 'monotonic', clock):
            a._handle(delta(LOUD))
            a._handle({'type': 'session.output_transcript.delta', 'delta': 'Hello'})
            a._handle(backend('response.completed'))
            self.assertIsNotNone(a._response_id)
            self.assertEqual(native_events, ['harness.pcm_speech_detected'])
            a._handle(delta(bytes(48000)))
            self.assertIsNotNone(a._response_id)  # a completed result that has not been voiced keeps the reply open
            clock.now += live.RESULT_VOICE_GRACE_S
            a._handle(delta(QUIET))
        events = drain(a)
        self.assertEqual(native_events, ['harness.pcm_speech_detected'])
        self.assertTrue(bakeoff._is_audio('openai-live', {'native_type': native_events[0]}))
        self.assertFalse(bakeoff._is_audio('openai-live', {'native_type': 'session.output_audio.delta'}))
        self.assertEqual([e.type for e in events].count('turn_end'), 1)
        self.assertEqual(next(e.text for e in events if e.type == 'transcript_final'), 'Hello')
        # silence beyond REPLY_SILENCE_MS is not part of the reply
        self.assertEqual(sum(e.sample_count for e in events if e.type == 'audio_out'), 2400 + 24000 * live.REPLY_SILENCE_MS // 1000)

    async def test_acknowledgement_cannot_finish_while_caller_is_speaking(self):
        a = OpenAILiveAdapter(api_key='offline')
        a._handle(delta(LOUD))
        with patch.object(live.time, 'monotonic', return_value=100):
            await a.send_audio(LOUD[:960], {'sample_count': 480})
            a._handle(delta(bytes(48000)))
        self.assertIsNotNone(a._response_id)
        self.assertEqual(a._reply_not_before, 100 + live.CALLER_GRACE_S)

    async def test_reply_boundary_rules_from_measured_pauses(self):
        a, clock = OpenAILiveAdapter(api_key='offline'), Clock()
        with patch.object(live.time, 'monotonic', clock):
            a._handle(delta(LOUD))
            for _ in range(7):  # the longest natural pause in the evaluation ledger (700 ms) does not end a reply
                a._handle(delta(QUIET))
            a._handle(delta(LOUD))
            self.assertEqual([e.type for e in drain(a)].count('turn_end'), 0)
            for _ in range(8):  # REPLY_SILENCE_MS does, with no backend work and no caller grace
                a._handle(delta(QUIET))
        self.assertEqual([e.type for e in drain(a)].count('turn_end'), 1)

    async def test_acknowledgement_and_delegated_result_stay_one_reply_without_wait_silence(self):
        a, clock = OpenAILiveAdapter(api_key='offline'), Clock()
        with patch.object(live.time, 'monotonic', clock):
            a._handle({'type': 'session.delegation.created'})
            a._handle(backend('response.created'))
            a._handle(delta(LOUD))  # "one moment"
            for _ in range(30):  # 3 s backend wait, longer than the silence rule
                clock.now += 0.1
                a._handle(delta(QUIET))
            a._handle(backend('response.completed'))
            for _ in range(9):  # 0.9 s from completion to the result being voiced (p95 0.95 s)
                clock.now += 0.1
                a._handle(delta(QUIET))
            self.assertIsNotNone(a._response_id)
            a._handle(delta(LOUD))  # the result
            for _ in range(8):  # voiced, so the ordinary silence rule ends it without waiting out the grace
                clock.now += 0.1
                a._handle(delta(QUIET))
        events = drain(a)
        self.assertEqual([e.type for e in events].count('turn_start'), 1)
        self.assertEqual([e.type for e in events].count('turn_end'), 1)
        audio = sum(e.sample_count for e in events if e.type == 'audio_out')
        # each pause (the 3.9 s wait, the closing silence) is passed on only up to REPLY_SILENCE_MS
        self.assertEqual(audio, 2 * 2400 + 2 * 24000 * live.REPLY_SILENCE_MS // 1000)

    async def test_tool_wait_and_pending_result_keep_the_reply_open(self):
        a, clock = OpenAILiveAdapter(api_key='offline'), Clock()
        with patch.object(live.time, 'monotonic', clock):
            a._handle(delta(LOUD))
            a._handle({'type': 'response.event', 'event': {'type': 'response.output_item.done', 'item': {
                'type': 'function_call', 'call_id': 'c1', 'name': 'verify_identity', 'arguments': '{}'}}})
            clock.now += 5
            a._handle(delta(bytes(48000)))
            self.assertIsNotNone(a._response_id)

    async def test_interrupt_closes_the_local_reply(self):
        a = OpenAILiveAdapter(api_key='offline')
        a._handle(delta(LOUD))
        first = a._item_id
        with self.assertRaises(CapabilityUnsupported):
            await a.interrupt(first, 0)
        self.assertIsNone(a._response_id)
        a._handle(delta(LOUD))  # the provider keeps talking: a new reply, not more of the cut-off one
        self.assertNotEqual(a._item_id, first)
        self.assertEqual([e.type for e in drain(a)], ['turn_start', 'audio_out', 'turn_end', 'turn_start', 'audio_out'])

    async def test_audio_clock_paces_by_deadline_and_never_splices_silence_into_caller_audio(self):
        a = OpenAILiveAdapter(api_key='offline')
        sent = []

        async def send(message):
            sent.append((time.monotonic(), base64.b64decode(message['audio'])))
            await asyncio.sleep(0.003)  # per-send overhead that used to accumulate as lag

        a._send = send
        pump = asyncio.create_task(a._audio_clock())
        try:
            start = time.monotonic()
            await a.send_audio(LOUD * 5, {'sample_count': 12000})  # 500 ms queued at once
            await a.request_turn()
            backlog = time.monotonic() - start
            for _ in range(10):  # a harness feeding slower than real time (25 ms per 20 ms frame)
                await a.send_audio(LOUD[:960], {'sample_count': 480})
                await asyncio.sleep(0.025)
            await a.request_turn()
        finally:
            a._closing = True
            await asyncio.gather(pump, return_exceptions=True)
        self.assertLess(backlog, 0.54)  # old pacing: 25 x (20 + 3) ms = 575 ms and growing with length
        caller = [i for i, (_, pcm) in enumerate(sent) if audio_activity.audible(pcm)]
        self.assertEqual(len(caller), 35)
        gaps = [i for i in range(caller[0], caller[-1]) if not audio_activity.audible(sent[i][1])]
        self.assertEqual(gaps, [])

    async def test_registry_and_transport_are_distinct_from_realtime(self):
        a, config = _adapter('openai-live', 'advanced-v1')
        self.assertIsInstance(a, OpenAILiveAdapter)
        transport = configure_transport('openai-live', config, 'fixed')
        self.assertEqual(transport['turn_commit'], 'provider_vad')
        self.assertFalse(transport['client_truncation'])
        self.assertEqual(capability_binding('openai-live')['capability_status'], 'verified')

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


class FirstAudibleAudio(unittest.TestCase):
    def test_audible_payload_by_shape(self):
        loud, quiet = base64.b64encode(LOUD).decode(), base64.b64encode(QUIET).decode()
        for wrap in (lambda d: {'type': 'response.output_audio.delta', 'delta': d},
                     lambda d: {'type': 'audio', 'audio_event': {'audio_base_64': d}},
                     lambda d: {'audioOutput': {'content': d}},
                     lambda d: {'serverContent': {'modelTurn': {'parts': [{'inlineData': {'mimeType': 'audio/pcm', 'data': d}}]}}}):
            self.assertIs(audio_activity.audible_payload(wrap(loud)), True)
            self.assertIs(audio_activity.audible_payload(wrap(quiet)), False)
        self.assertIsNone(audio_activity.audible_payload({'type': 'response.done'}))

    def test_first_audio_uses_the_audible_flag_when_recorded(self):
        def native(ms, audible):
            return {'type': 'adapter.native_event', 'content': {'native_type': 'response.output_audio.delta', 'payload_bytes': 900,
                                                                'received_ms': ms, 'audio_bytes': 600, 'audible': audible}}
        events = [{'type': 'input.caller_audio', 'content': {'stream_started_ms': 0, 'stream_ended_ms': 2200, 'speech_ended_ms': 1000,
                                                              'turn_commit': 'explicit', 'commit_delay_ms': 500}},
                  native(1300, False), native(1600, True)]
        self.assertEqual(bakeoff.first_audio_turns('openai-realtime-live', events)[0]['first_audio_ms'], 600)
        legacy = [events[0], *({**e, 'content': {k: v for k, v in e['content'].items() if k != 'audible'}} for e in events[1:])]
        self.assertEqual(bakeoff.first_audio_turns('openai-realtime-live', legacy)[0]['first_audio_ms'], 300)


class ComponentIndependence(unittest.TestCase):
    def judge(self, model, provider='openai', family='gpt'):
        return JudgeConfig(f'{provider}-{model}', provider, provider, model, family)

    def test_delegated_backend_is_the_tested_model(self):
        tested = TESTED_MODELS['openai-live']
        out = assess(tested, [self.judge('gpt-6-luna'), self.judge('gpt-6-sol')], required_judges=1,
                     material_benchmark=False, order_seed=0, run_session_id='run')
        self.assertEqual(out['judges'][0]['reason_codes'], ['JUDGE_IS_TESTED_MODEL'])
        self.assertTrue(out['judges'][1]['eligible'])
        self.assertTrue(is_tested_model(self.judge('gpt-live-1'), tested))
        self.assertFalse(is_tested_model(self.judge('gpt-6-luna'), TESTED_MODELS['openai-realtime-live']))
        TestedModel.model_validate({k: v for k, v in out['tested'].items()})

    def test_elevenlabs_agent_llm_is_a_tested_component(self):
        tested = TESTED_MODELS['elevenlabs-live']
        gemini = self.judge('gemini-3.5-flash', 'google', 'gemini')
        out = assess(tested, [gemini], required_judges=1, material_benchmark=True, order_seed=0, run_session_id='run')
        self.assertEqual(out['judges'][0]['reason_codes'], ['JUDGE_IS_TESTED_MODEL'])
        self.assertEqual(tested_identity(tested)['components'][0]['vendor'], 'google')
        other = assess(tested, [self.judge('gemini-3-pro', 'google', 'gemini')], required_judges=1, material_benchmark=True,
                       order_seed=0, run_session_id='run')
        self.assertEqual(other['judges'][0]['reason_codes'], ['SAME_VENDOR_IN_MATERIAL_BENCHMARK'])


class Disclosure(unittest.TestCase):
    def test_wrapper_and_context_channel_are_disclosed(self):
        binding = session_metadata('openai-live', 'gpt-live-1', {})
        self.assertEqual(binding['context_channel'], 'instructions_append')
        parity = bakeoff_disclosure.parity({**binding, 'sampling': {}, 'confirmation_delivery': 'context_only'})
        realtime = bakeoff_disclosure.parity({**session_metadata('openai-realtime-live', 'm', {}), 'sampling': {},
                                              'confirmation_delivery': 'context_only'})
        out = bakeoff_disclosure.disclose(['openai-live', 'openai-realtime-live'], [
            {'provider': 'openai-live', 'parity': parity}, {'provider': 'openai-realtime-live', 'parity': realtime}])
        live_row = out['providers'][0]
        self.assertEqual(live_row['prompt_delta'], [f'[adapter] {LIVE_DELEGATION_WRAPPER.strip()}'])
        self.assertEqual(live_row['confirmation_delivery'], 'context_only via instructions_append')
        self.assertIn('prompt_delta', out['differs'])
        self.assertIn('confirmation_delivery', out['differs'])
        self.assertTrue(any('gpt-live-delegation-v1' in t for t in out['limitations']))
        self.assertTrue(any('billed by session duration' in t for t in out['limitations']))


if __name__ == '__main__':
    unittest.main()
