import asyncio
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from voice_harness.api import create_app
from voice_harness.provider_balances import (
    BalanceService,
    BalanceSettings,
    aws_balance,
    google_balance,
    xai_balance,
)
from voice_harness.storage import Database


class Balances(unittest.IsolatedAsyncioTestCase):
    async def test_xai_sign_currency_and_no_inference_key_fallback(self):
        self.assertEqual((await xai_balance({'xAI_API_KEY': 'inference'}))['status'], 'setup_required')
        for raw, expected in [('-2317', 23.17), ('0', 0), ('500', -5)]:
            request = httpx.Request('GET', 'https://management-api.x.ai/')
            with patch('httpx.AsyncClient.get', new=AsyncMock(return_value=httpx.Response(200, json={'total': {'val': raw}}, request=request))) as get:
                result = await xai_balance({'XAI_MANAGEMENT_API_KEY': 'secret', 'XAI_TEAM_ID': 'team/unsafe'})
            self.assertEqual(result['balance'], expected)
            self.assertEqual(result['currency'], 'USD')
            self.assertIn('team%2Funsafe', get.call_args.args[0])
            self.assertNotIn('secret', json.dumps(result))

    async def test_partial_failure_and_coalesced_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            service = BalanceService(Database(str(Path(directory) / 't.sqlite')))
            with patch('voice_harness.provider_balances.xai_balance', new=AsyncMock(side_effect=httpx.ReadTimeout('SECRET')) ) as xai, patch('voice_harness.provider_balances.aws_balance', return_value={'status': 'live', 'balance': 0, 'currency': 'USD'}):
                results = await asyncio.gather(service.snapshot(), service.snapshot())
                self.assertEqual(xai.await_count, 1)
            self.assertEqual(results[0]['items'][1]['status'], 'unavailable')
            self.assertEqual(results[0]['items'][3]['balance'], 0)
            self.assertNotIn('SECRET', json.dumps(results))
            self.assertIsNone(results[0]['items'][0]['balance'])

    async def test_malformed_is_unknown_not_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            service = BalanceService(Database(str(Path(directory) / 't.sqlite')))
            request = httpx.Request('GET', 'https://management-api.x.ai/')
            for raw in ('NaN', 'Infinity', 'nope', None, True):
                with patch.dict('os.environ', {'XAI_MANAGEMENT_API_KEY':'secret', 'XAI_TEAM_ID':'t'}), patch('httpx.AsyncClient.get', new=AsyncMock(return_value=httpx.Response(200, json={'total': {'val':raw}}, request=request))):
                    _, reading = await service._query('xai')
                self.assertEqual(reading['status'], 'unavailable')
                self.assertNotIn('balance', reading)

    async def test_manual_settings_persist_without_redating(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / 't.sqlite'))
            service = BalanceService(db)
            service.save('openai', BalanceSettings(manual_balance=0, record_balance=True))
            with db.connection() as conn:
                before = dict(conn.execute('SELECT * FROM harness_balance_settings').fetchone())
            service.save('openai', BalanceSettings(threshold=5))
            with db.connection() as conn:
                after = dict(conn.execute('SELECT * FROM harness_balance_settings').fetchone())
            self.assertEqual(before['recorded_at'], after['recorded_at'])
            self.assertEqual(after['manual_balance'], 0)
            other = BalanceService(db)
            with patch.object(other, '_query', side_effect=lambda p: (p, {'status':'unavailable'})):
                item = (await other.snapshot())['items'][0]
            self.assertEqual(item['manual_balance'], 0)
            self.assertIsNone(item['balance'])
            self.assertEqual(item['status'], 'unsupported')
            service.save('openai', BalanceSettings(record_balance=True))
            with db.connection() as conn:
                self.assertIsNone(conn.execute('SELECT * FROM harness_balance_settings').fetchone()['recorded_at'])


class BalanceApi(unittest.TestCase):
    def test_google_cost_export_reads_net_month_to_date_spend(self):
        request = httpx.Request('POST', 'https://bigquery.googleapis.com/bigquery/v2/projects/voiceharness/queries')
        response = httpx.Response(200, json={
            'jobComplete': True,
            'rows': [{'f': [{'v': '12.34'}, {'v': 'GBP'}]}],
        }, request=request)
        with patch('voice_harness.adapters.google_gemini_live._fetch_access_token', return_value='access-token'), patch('httpx.Client.post', return_value=response) as post:
            result = google_balance({'GOOGLE_BILLING_PROJECT_ID': 'voiceharness', 'GOOGLE_BILLING_DATASET': 'voice_billing'})
        self.assertEqual(result['status'], 'live')
        self.assertEqual(result['balance'], 12.34)
        self.assertEqual(result['currency'], 'GBP')
        self.assertEqual(result['kind'], 'spend')
        self.assertIn('voiceharness.voice_billing.gcp_billing_export_v1_*', post.call_args.kwargs['json']['query'])
        self.assertNotIn('access-token', json.dumps(result))

    def test_google_cost_export_requires_configuration_and_handles_not_yet_enabled_export(self):
        self.assertEqual(google_balance({})['status'], 'setup_required')
        request = httpx.Request('POST', 'https://bigquery.googleapis.com/bigquery/v2/projects/voiceharness/queries')
        response = httpx.Response(404, json={'error': {'message': 'Not found'}}, request=request)
        with patch('voice_harness.adapters.google_gemini_live._fetch_access_token', return_value='access-token'), patch('httpx.Client.post', return_value=response):
            result = google_balance({'GOOGLE_BILLING_PROJECT_ID': 'voiceharness', 'GOOGLE_BILLING_DATASET': 'voice_billing'})
        self.assertEqual(result['status'], 'setup_required')
        self.assertNotIn('balance', result)

    def test_auth_validation_and_static_page(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(Database(str(Path(directory) / 't.sqlite')), directory, 'op', reviewer_token='review')
            payload = {'items': [], 'generated_at': time.time()}
            with TestClient(app) as client, patch.object(app.state.provider_balances, 'snapshot', new=AsyncMock(return_value=payload)) as read:
                path = '/api/v1/provider-balances'
                self.assertEqual(client.get(path).status_code, 401)
                self.assertEqual(client.get(path, headers={'Authorization': 'Bearer review'}).status_code, 403)
                read.assert_not_called()
                headers = {'Authorization': 'Bearer op'}
                response = client.get(path, headers=headers)
                self.assertEqual(response.json(), payload)
                self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertEqual(client.put(path+'/openai', headers={'Authorization':'Bearer review'}, json={}).status_code, 403)
                self.assertEqual(client.put(path+'/unknown', headers=headers, json={}).status_code, 404)
                for body in ({'threshold':-1}, {'manual_balance':'NaN'}, {'currency':'ABC'}, {'key':'secret'}):
                    self.assertEqual(client.put(path+'/openai', headers=headers, json=body).status_code, 422)
                self.assertEqual(client.put(path+'/openai', headers=headers, json={'manual_balance':20, 'record_balance':True}).status_code, 200)
                self.assertEqual(client.put(path+'/openai', headers=headers, json={'currency':'GBP'}).status_code, 422)
                self.assertEqual(client.get('/client/balances.html').status_code, 200)

    def test_aws_cost_explorer_uses_dedicated_credentials_and_reports_month_spend(self):
        body = {'ResultsByTime': [{'Total': {'NetUnblendedCost': {'Amount': '5.15', 'Unit': 'USD'}}, 'Estimated': True}]}
        env = {'AWS_billing_access_key_id': 'billing-key', 'AWS_billing_secret_access_key': 'billing-secret',
               'AWS_ACCESS_KEY_ID': 'bedrock-key', 'AWS_SECRET_ACCESS_KEY': 'bedrock-secret', 'AWS_PROFILE': 'bedrock'}
        with patch('shutil.which', return_value='/usr/bin/aws'), patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, json.dumps(body), '')) as run:
            result = aws_balance(env)
        self.assertEqual(result['status'], 'live')
        self.assertEqual(result['balance'], 5.15)
        self.assertEqual(result['kind'], 'spend')
        self.assertIn('ce', run.call_args.args[0])
        self.assertIn('get-cost-and-usage', run.call_args.args[0])
        self.assertNotIn('shell', run.call_args.kwargs)
        self.assertEqual(run.call_args.kwargs['env']['AWS_ACCESS_KEY_ID'], 'billing-key')
        self.assertEqual(run.call_args.kwargs['env']['AWS_SECRET_ACCESS_KEY'], 'billing-secret')
        self.assertNotIn('AWS_PROFILE', run.call_args.kwargs['env'])

    def test_aws_permission_failure_is_actionable_without_raw_errors(self):
        env = {'AWS_billing_access_key_id': 'billing-key', 'AWS_billing_secret_access_key': 'billing-secret'}
        with patch('shutil.which', return_value='/usr/bin/aws'), patch('subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', 'AccessDenied SECRET')):
            result = aws_balance(env)
        self.assertIn('ce:GetCostAndUsage', result['message'])
        self.assertNotIn('SECRET', json.dumps(result))
