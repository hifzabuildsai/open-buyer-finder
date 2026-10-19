import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch

from buyerfind.market import validate_config
from buyerfind.net import FetchError
from buyerfind.providers import Providers


class ProviderTests(unittest.TestCase):
    def test_local_ollama_and_searx_use_actual_http_contract(self):
        requests=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                requests.append(('GET',self.path,None))
                data={'results':[{'url':'https://acme.example.com','title':'Acme','content':'Company description'}]}
                self.send_response(200);self.end_headers();self.wfile.write(json.dumps(data).encode())
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(('POST',self.path,body))
                self.send_response(200);self.end_headers();self.wfile.write(json.dumps({'message':{'content':'{"answer":"ok"}'}}).encode())
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            endpoint=f'http://127.0.0.1:{server.server_port}'
            p=Providers(validate_config({'model':{'endpoint':endpoint},'search':{'endpoint':endpoint}}))
            self.assertEqual(p.ask('test',{'content':'untrusted'})['answer'],'ok')
            self.assertEqual(p.search('automation companies')[0]['url'],'https://acme.example.com')
            self.assertEqual(requests[0][1],'/api/chat')
            self.assertFalse(requests[0][2]['stream'])
            self.assertEqual(requests[0][2]['format'],'json')
            self.assertIn('format=json',requests[1][1])
            self.assertEqual(p.usage,{'model_calls':1,'search_requests':1})
        finally:
            server.shutdown();server.server_close();worker.join()

    def test_compatible_model_and_brave_credentials_stay_on_selected_adapter(self):
        calls=[]
        def http(method,url,**kwargs):
            calls.append((method,url,kwargs))
            if 'chat/completions' in url:return {'choices':[{'message':{'content':'{"ok":true}'}}]}
            return {'web':{'results':[]}}
        cfg=validate_config({'model':{'provider':'openai_compatible','endpoint':'https://model.example.com/v1'},'search':{'provider':'brave'}})
        with patch.dict(os.environ,{'BUYERFIND_LLM_KEY':'model-test-secret','BRAVE_SEARCH_API_KEY':'search-test-secret'}),patch('buyerfind.providers.json_request',side_effect=http):
            p=Providers(cfg);p.ask('test',{});p.search('test')
        self.assertEqual(calls[0][2]['headers']['Authorization'],'Bearer model-test-secret')
        self.assertEqual(calls[1][2]['headers'],{'X-Subscription-Token':'search-test-secret'})
        self.assertNotIn('secret',json.dumps(p.usage))
        self.assertEqual(calls[0][1],'https://model.example.com/v1/chat/completions')

    def test_email_verification_is_address_specific_and_catchall_is_not_valid(self):
        cfg=validate_config({'enrichment':{'provider':'hunter'}})
        email='owner@company.example.com'
        with patch.dict(os.environ,{'HUNTER_API_KEY':'unit-key'}):
            for status,extra,expected in [('valid',{},'valid'),('valid',{'accept_all':True},'accept_all'),('unknown',{},'unknown'),('valid',{'result':'undeliverable'},'unknown')]:
                with self.subTest(status=status,extra=extra),patch('buyerfind.providers.json_request',return_value={'data':{'email':email,'status':status,**extra}}):
                    self.assertEqual(Providers(cfg).verify(email)['status'],expected)
            with patch('buyerfind.providers.json_request',return_value={'data':{'email':'other@company.example.com','status':'valid'}}):
                with self.assertRaisesRegex(FetchError,'exact requested email'):
                    Providers(cfg).verify(email)

    def test_public_mode_never_calls_verifier(self):
        with patch('buyerfind.providers.json_request',side_effect=AssertionError('must not call')):
            p=Providers(validate_config({}))
            self.assertEqual(p.verify('owner@example.com')['status'],'published_unverified')
            self.assertEqual(p.enrich('example.com'),[])
            self.assertEqual(p.usage,{})

    def test_model_invalid_json_is_a_visible_failure(self):
        with patch('buyerfind.providers.json_request',return_value={'message':{'content':'I think the answer is'}}):
            with self.assertRaisesRegex(FetchError,'valid JSON'):
                Providers(validate_config({})).ask('test',{})


if __name__=='__main__':unittest.main()
