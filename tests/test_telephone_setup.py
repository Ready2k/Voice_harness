"""Goals/details are bound to one call; values cannot be substituted by the agent."""
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from pydantic import ValidationError

from voice_harness.delegate_dialogue import DelegateDialogue
from voice_harness.telephone_setup import CallSetup, CallDetail, FixedResponse, setup_templates
from voice_harness.telephony_demo import DelegateDemo


class SetupTests(unittest.IsolatedAsyncioTestCase):
    def demo(self, setup, scenario='balance', adaptive=True):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        last = ['']
        def synth(text):
            last[0] = text
            return text.encode()
        asr = Mock()
        asr.available.return_value = (True, None)
        asr.transcribe.side_effect = lambda pcm, rate: SimpleNamespace(text=pcm.decode())
        adapter = AsyncMock()
        adapter.open_session.return_value = {'session_id': 'offline'}
        async def receive():
            if False:
                yield None
        adapter.receive = receive
        demo = DelegateDemo({}, Path(temp.name) / 'evidence', destination='01617683827', caller_id='01483694454',
            route_id='770222', scenario=scenario, task=setup.goal, call_setup=setup,
            brief=setup.brief(scenario) if adaptive else None, adapter=adapter, control=AsyncMock(), asr=asr,
            synthesise=synth, reviewer=SimpleNamespace(review=AsyncMock(return_value='allow'), model='offline'))
        demo.active = True
        demo.sent_values = {'test_route'}
        demo.socket = AsyncMock()
        demo.stream_ready.set()
        demo.stream_sid = 'MZoffline'
        return demo

    def prompt(self, demo, text):
        key = f'prompt_{len(demo.prompt_events)}'
        demo.prompt_events.append(key)
        demo.prompt_text[key] = text
        return key

    async def test_balance_goal_and_identifier_references_reach_agent_without_values(self):
        setup = CallSetup(goal='Check the balance and currency.', details=[CallDetail(label='Account ID', value='001234567', kind='identifier', when_asked='account number')])
        demo = self.demo(setup)
        await demo.prepare()
        await asyncio.gather(*demo.tasks)
        config = demo.adapter.open_session.await_args.args[0]
        self.assertIn(setup.goal, config['instructions'])
        self.assertIn('phone_provide_detail', {t['name'] for t in config['tools']})
        self.assertIn('detail_0', config['instructions'])
        self.assertNotIn('001234567', json.dumps(config))
        self.assertNotIn('zero, zero, one, two', json.dumps([e.content for e in demo.ledger.events]))
        event = demo.record('test.redaction', {'text': 'zero zero one two three four five six seven'})
        self.assertNotIn('zero zero one', event.content['text'])
        demo.play_phrase = AsyncMock(return_value={'transport_acknowledged': True})
        prompt = self.prompt(demo, 'Please enter your account ID.')
        await demo.execute('phone_provide_detail', {'detail_ref': 'detail_0', 'prompt_event_id': prompt, 'terminator': ''})
        demo.play_phrase.assert_awaited_once_with('detail_0')
        with self.assertRaisesRegex(ValueError, 'REPLAY'):
            await demo.execute('phone_provide_detail', {'detail_ref': 'detail_0', 'prompt_event_id': prompt, 'terminator': ''})

    async def test_passcode_keypad_only_on_matching_current_request_and_no_uncertain_retry(self):
        setup = CallSetup(goal='Check balance.', details=[CallDetail(label='Passcode', value='0039', kind='passcode', delivery='dtmf', when_asked='passcode')])
        demo = self.demo(setup)
        demo.send_digits = AsyncMock(return_value={'call_update': 'accepted'})
        for question in ['What is your account number?', 'Your passcode has been accepted.', 'Do not provide your passcode.', 'Give your passcode to cancel the card.']:
            prompt = self.prompt(demo, question)
            with self.assertRaises(ValueError):
                await demo.execute('phone_provide_detail', {'detail_ref': 'detail_0', 'prompt_event_id': prompt, 'terminator': ''})
        demo.send_digits.assert_not_awaited()
        prompt = self.prompt(demo, 'Please enter your passcode followed by hash.')
        demo.send_digits.side_effect = TimeoutError()
        args = {'detail_ref': 'detail_0', 'prompt_event_id': prompt, 'terminator': '#'}
        with self.assertRaises(TimeoutError):
            await demo.execute('phone_provide_detail', args)
        demo.send_digits.assert_awaited_once_with('0039#')
        with self.assertRaisesRegex(ValueError, 'REPLAY'):
            await demo.execute('phone_provide_detail', args)

    async def test_static_uses_exact_operator_phrases_and_has_no_detail_bypass(self):
        setup = CallSetup(goal='Get help for a missing Visa debit card.', fixed_responses=[FixedResponse(when_asked='which card', text='It is my Visa debit card.')],
            details=[CallDetail(label='Account', value='1234', kind='identifier', when_asked='account number')])
        demo = self.demo(setup, 'lost-wallet', adaptive=False)
        await demo.prepare()
        await asyncio.gather(*demo.tasks)
        config = demo.adapter.open_session.await_args.args[0]
        self.assertIn('It is my Visa debit card.', config['instructions'])
        speak = next(t for t in config['tools'] if t['name'] == 'phone_speak')
        self.assertNotIn('detail_0', speak['parameters']['properties']['phrase']['enum'])
        with self.assertRaisesRegex(ValueError, 'OUTSIDE_MANDATE'):
            await demo.execute('phone_speak', {'phrase': 'detail_0'})

    def test_validation_and_templates_do_not_invent_verification_details(self):
        for template in setup_templates().values():
            setup = CallSetup.model_validate(template)
            self.assertTrue(setup.goal)
            self.assertFalse(any(d.kind != 'fact' for d in setup.details))
        with self.assertRaises(ValidationError):
            CallDetail(label='PIN', value='1234', kind='passcode')
        with self.assertRaises(ValidationError):
            CallDetail(label='Account', value='ABC123', delivery='dtmf')
        setup = CallSetup(goal='Balance', details=[CallDetail(label='PIN', value='0098', kind='passcode', when_asked='passcode')])
        self.assertNotIn('0098', json.dumps(setup.public()))
        dialogue = DelegateDialogue(CallSetup(goal='Balance').brief('balance'))
        self.assertEqual(dialogue.items, {})
        self.assertEqual(DelegateDemo.detail_speech('AC001'), 'A, C, zero, zero, one.')
        self.assertTrue(DelegateDemo.detail_transcript_matches('AC001', 'AC zero zero one'))
        self.assertFalse(DelegateDemo.detail_transcript_matches('AC001', 'AC zero one'))
