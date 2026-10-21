import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from voice_harness.telephone_api import TelephoneCalls, TelephoneRequest, register_telephone_routes


class TelephoneTests(unittest.IsolatedAsyncioTestCase):
    async def test_runner_arguments_and_duplicate_call_guard(self):
        with tempfile.TemporaryDirectory() as root:
            calls = TelephoneCalls(root)
            process = Mock()
            process.returncode = None
            process.stdin.drain = AsyncMock()
            with patch.object(calls, 'configuration', return_value=({'OPENAI_API_KEY': 'offline'}, '/test/cloudflared', None, [])), patch('asyncio.create_subprocess_exec', return_value=process) as spawn:
                result = await calls.start(TelephoneRequest(destination='01617683827', caller_id='01483694454', route_id='770222', subtest_id='01'))
                self.assertEqual(result['state'], 'running')
                args = spawn.call_args.args
                self.assertIn('+441617683827', args)
                self.assertIn('01', args)
                self.assertIn('--call-setup-stdin', args)
                self.assertIn('--tunnel-binary', args)
                self.assertTrue(all(isinstance(arg, str) for arg in args))
                with self.assertRaises(HTTPException):
                    await calls.start(TelephoneRequest(destination='01617683827', caller_id='01483694454', route_id='770222'))
                await calls.stop()
                process.send_signal.assert_called_once()

    async def test_missing_configuration_never_spawns(self):
        with tempfile.TemporaryDirectory() as root:
            calls = TelephoneCalls(root)
            with patch.object(calls, 'configuration', return_value=({}, None, None, ['key'])), patch('asyncio.create_subprocess_exec') as spawn:
                with self.assertRaises(HTTPException):
                    await calls.start(TelephoneRequest(destination='01617683827', caller_id='01483694454', route_id='770222'))
                spawn.assert_not_called()

    def test_auth_artifact_allowlist_and_validation(self):
        with tempfile.TemporaryDirectory() as root:
            app = FastAPI()
            def auth():
                raise HTTPException(401)
            register_telephone_routes(app, root, auth)
            client = TestClient(app)
            self.assertEqual(client.get('/api/v1/telephone').status_code, 401)
            app.dependency_overrides[auth] = lambda: None
            call_id = 'a' * 32
            folder = Path(root) / 'telephone-calls' / call_id
            folder.mkdir()
            (folder / 'runner.log').write_text('private')
            self.assertEqual(client.get(f'/api/v1/telephone/{call_id}/artifacts/runner.log').status_code, 404)
            for values in ({'route_id': '1'}, {'max_seconds': 999}, {'routing_mode': 'other'}, {'tunnel_binary': '/bad'}):
                with self.assertRaises(ValidationError):
                    TelephoneRequest(**({'destination': '01617683827', 'caller_id': '01483694454', 'route_id': '770222'} | values))

    async def test_twilio_numbers_filter_and_restricted_key_fallback(self):
        import httpx
        with tempfile.TemporaryDirectory() as root:
            calls = TelephoneCalls(root)
            env = {'TWILIO_ACCOUNT_SID': 'ACtest', 'TWILIO_API_KEY_SID': 'SKtest', 'TWILIO_API_KEY_SECRET': 'key-secret', 'TWILIO_AUTH_TOKEN': 'auth-secret'}
            client = AsyncMock()
            client.get.side_effect = [httpx.Response(403), httpx.Response(200, json={
                'incoming_phone_numbers': [
                    {'phone_number': '+441483694454', 'friendly_name': 'Demo', 'capabilities': {'voice': True}},
                    {'phone_number': '+441111111111', 'capabilities': {'voice': False}}], 'next_page_uri': None})]
            context = AsyncMock()
            context.__aenter__.return_value = client
            with patch.object(calls, 'configuration', return_value=(env, None, None, [])), patch('voice_harness.telephone_api.httpx.AsyncClient', return_value=context):
                result = await calls.twilio_numbers()
            self.assertEqual(result['caller_id'], '+441483694454')
            self.assertEqual(len(result['items']), 1)
            self.assertEqual(client.get.call_args.kwargs['auth'], ('ACtest', 'auth-secret'))
            self.assertNotIn('secret', str(result))
            self.assertEqual(result['scenario_defaults']['lost-wallet']['subtest_id'], '01')

    async def test_static_does_not_pass_adaptive_brief_and_provider_is_forwarded(self):
        with tempfile.TemporaryDirectory() as root:
            calls = TelephoneCalls(root)
            process = Mock(returncode=None)
            process.stdin.drain = AsyncMock()
            options = [{'provider_config_id': 'gemini-live', 'missing': []}]
            with patch.object(calls, 'configuration', return_value=({}, '/test/cloudflared', None, [])), patch('voice_harness.telephone_api.provider_options', return_value=options), patch('asyncio.create_subprocess_exec', return_value=process) as spawn:
                await calls.start(TelephoneRequest(destination='01617683827', caller_id='01483694454', route_id='770222', provider_config_id='gemini-live', response_mode='static'))
                args = spawn.call_args.args
                self.assertNotIn('--brief', args)
                self.assertEqual(args[args.index('--provider-config-id') + 1], 'gemini-live')
                self.assertFalse(Path(args[args.index('--output') + 1]).exists(), 'DelegateDemo creates its own evidence directory')

    async def test_unavailable_provider_never_dials_or_falls_back(self):
        with tempfile.TemporaryDirectory() as root:
            calls = TelephoneCalls(root)
            with patch.object(calls, 'configuration', return_value=({}, '/test/cloudflared', None, [])), patch('voice_harness.telephone_api.provider_options', return_value=[{'provider_config_id': 'nova-sonic-live', 'missing': ['NO_AWS_SESSION']}]), patch('asyncio.create_subprocess_exec') as spawn:
                with self.assertRaises(HTTPException) as failure:
                    await calls.start(TelephoneRequest(destination='01617683827', caller_id='01483694454', route_id='770222', provider_config_id='nova-sonic-live'))
                self.assertIn('NO_AWS_SESSION', failure.exception.detail)
                spawn.assert_not_called()

    async def test_verification_values_use_private_pipe_and_redacted_history(self):
        import json

        from voice_harness.telephone_setup import CallDetail, CallSetup
        with tempfile.TemporaryDirectory() as root:
            calls = TelephoneCalls(root)
            process = Mock(returncode=None)
            process.stdin.drain = AsyncMock()
            setup = CallSetup(goal='Check my balance.', details=[CallDetail(label='PIN', value='001928', kind='passcode', delivery='dtmf', when_asked='passcode')])
            with patch.object(calls, 'configuration', return_value=({'OPENAI_API_KEY': 'offline'}, '/test/cloudflared', None, [])), patch('asyncio.create_subprocess_exec', return_value=process) as spawn:
                result = await calls.start(TelephoneRequest(destination='01617683827', caller_id='01483694454', route_id='770222', scenario='balance', call_setup=setup))
            self.assertNotIn('001928', str(spawn.call_args))
            self.assertNotIn('001928', json.dumps(result))
            self.assertNotIn('001928', (Path(root) / 'telephone-calls' / result['call_id'] / 'setup.json').read_text())
            self.assertEqual(json.loads(process.stdin.write.call_args.args[0])['details'][0]['value'], '001928')
            process.stdin.close.assert_called_once()
