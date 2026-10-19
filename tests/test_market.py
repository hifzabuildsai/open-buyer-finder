import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from buyerfind.core import now
from buyerfind.market import (demo_bundle, load_config, proof, run_pipeline,
                              summarise, validate_config, monitor_run)
from buyerfind.net import FetchError, request, validate_url
from buyerfind.providers import BudgetExceeded, Providers, parse_page


class ResearchBoundary:
    """Fictional external responses; the actual pipeline/validation/export runs."""
    def __init__(self, count=2, verified=True, fail_model=False, forged=False):
        self.usage, self.count, self.verified = {}, count, verified
        self.fail_model, self.forged = fail_model, forged

    def page(self, url):
        self.usage['pages']=self.usage.get('pages',0)+1
        domain=url.split('/')[2]
        name='Fourgate' if domain=='product.example.com' else domain.split('.')[0].title()
        text=f'{name} builds agent write verification. {name} uses PostgreSQL. {name} is based in London. Ada leads operations at {name}. ada@{domain}. Pricing starts at $29 per month. We launched 2026-10-01. '+(' '.join(f'Company {i}' for i in range(3)))
        return {'url':url,'title':name,'text':text,'links':[f'https://www.linkedin.com/company/{name.lower()}',f'https://www.linkedin.com/in/ada-{name.lower()}'], 'fetched_at':now(),'kind':'page'}

    def search(self,query):
        self.usage['search_requests']=self.usage.get('search_requests',0)+1
        if 'competitor' in query:
            return [self.page('https://rival.example.com')]
        return [self.page(f'https://company{i}.example.com') for i in range(min(self.count,20))]

    def ask(self,task,data):
        self.usage['model_calls']=self.usage.get('model_calls',0)+1
        if self.fail_model:
            raise FetchError('Model service unavailable')
        if task.startswith('PRODUCT'):
            source=data['sources'][0]
            f=lambda value: {'value':value,'source_url':source['url'],'quote':source['text']}
            return {'product':{'name':f('Fourgate'),'description':f('builds agent write verification'),'capabilities':[f('agent write verification')],'exclusions':[]},
                    'segments':[{'name':f'Segment {i}','buyer_role':'Workflow owner','pain':'Unverified writes','include':['writes'],'exclude':['read only'],'queries':['find companies'],'angle':'Checking outcomes'} for i in range(6)],'competitor_queries':['competitors']}
        if task.startswith('SEQUENCES'):
            return {'sequences':[{'name':f'Variant {i}','channel':'email' if i<2 else 'linkedin','steps':[{'day':d,'subject':'Checking writes','body':'Hi {{first_name}}, how does {{company_name}} check writes? Reply no to stop.'} for d in (0,3,7)]} for i in range(3)]}
        if task.startswith('CANDIDATES'):
            return {'candidates':[{'website':s['url'],'reason':'Cited category match','relationship':'adjacent'} for s in data['sources']]}
        source=data['sources'][0]
        name=source['title']
        f=lambda value: {'value':value,'source_url':source['url'],'quote':source['text'] if not self.forged else 'This quotation was fabricated.'}
        linked=lambda url: {'value':url,'source_url':source['url'],'quote':url}
        if task.startswith('COMPANY'):
            domain=source['url'].split('/')[2]
            return {'name':f(name),'industry':f('agent write verification'),'location':f('London'),
                    'linkedin':linked(source['links'][0]),'fit_status':'potential_fit','fit_reason':'Possible workflow fit, unverified demand.',
                    'fit_evidence':f('writes'),'tech_stack':[f('PostgreSQL')],
                    'contacts':[{'name':f('Ada'),'role':f('operations'),'email':f('ada@'+domain),'linkedin':linked(source['links'][1])}],
                    'triggers':[{'kind':'launch','summary':'Launch announcement','event_date':'2026-10-01','source_url':source['url'],'quote':'We launched 2026-10-01.'}]}
        if task.startswith('COMPETITOR'):
            return {'name':f(name),'pricing':f('$29 per month'),'features':[f('agent write verification')],'relationship':'adjacent','reason':'Similar problem category','review_gaps':[]}
        if task.startswith('TRIGGERS'):
            return {'triggers':[{'kind':'launch','summary':'Launch announcement','event_date':'2026-10-01','source_url':source['url'],'quote':'We launched 2026-10-01.'}]}
        raise AssertionError(task)

    def enrich(self,domain):
        return []

    def verify(self,email):
        return {'status':'valid' if self.verified else 'published_unverified','provider':'fixture-verifier' if self.verified else None,'checked_at':now() if self.verified else None}


class MarketTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out=Path(self.tmp.name)/'run'
        self.cfg=validate_config({'limits':{'companies':2,'contacts':2,'competitors':1}})

    def run_pipeline(self,providers=None,cfg=None):
        return run_pipeline('https://product.example.com',cfg or self.cfg,self.out,providers=providers or ResearchBoundary())

    def test_url_to_full_pack_real_pipeline_fake_provider_boundary(self):
        b=self.run_pipeline()
        self.assertEqual(b['status'],'complete',b['warnings'])
        self.assertEqual(b['summary']['enriched_companies'],2)
        self.assertEqual(b['summary']['verified_emails'],2)
        self.assertEqual(b['summary']['people'],2)
        self.assertEqual(len(b['sequences']),18)
        self.assertEqual(sum(len(s['steps']) for s in b['sequences']),54)
        self.assertEqual(len(b['segments']),6)
        self.assertEqual(b['competitors'][0]['pricing'],'$29 per month')
        self.assertTrue((self.out/'report.html').exists())
        self.assertTrue((self.out/'contacts.csv').exists())
        self.assertNotIn('fixture-verifier',(self.out/'companies.csv').read_text())
        self.assertTrue(all(c['evidence'] for c in b['companies']))

    def test_one_hundred_distinct_companies_and_fifty_person_shortlist(self):
        cfg=validate_config({'limits':{'companies':100,'contacts':50,'competitors':1},
                             'company_urls':[f'https://company{i}.example.com' for i in range(100)]})
        b=self.run_pipeline(ResearchBoundary(count=100),cfg)
        self.assertEqual(b['summary']['companies'],100)
        self.assertEqual(b['summary']['enriched_companies'],100)
        self.assertEqual(b['summary']['people'],50)
        self.assertEqual(len({c['domain'] for c in b['companies']}),100)

    def test_free_public_addresses_never_become_verified(self):
        b=self.run_pipeline(ResearchBoundary(verified=False))
        self.assertEqual(b['status'],'partial')
        self.assertEqual(b['summary']['verified_emails'],0)
        self.assertEqual(b['summary']['outreach_ready_companies'],0)
        self.assertFalse(b['summary']['target_met'])
        self.assertTrue(any('enriched_companies' in x for x in b['summary']['shortfalls']))

    def test_provider_failure_preserves_visible_blocker(self):
        b=self.run_pipeline(ResearchBoundary(fail_model=True))
        self.assertEqual(b['status'],'blocked')
        self.assertIn('Model service unavailable',b['warnings'])
        self.assertTrue((self.out/'bundle.json').exists())
        self.assertFalse(b['summary']['target_met'])

    def test_fabricated_company_quotes_are_rejected(self):
        b=self.run_pipeline(ResearchBoundary(forged=True))
        self.assertEqual(b['companies'],[])
        self.assertEqual(b['summary']['verified_emails'],0)

    def test_existing_run_is_never_overwritten(self):
        self.out.mkdir()
        (self.out/'keep.txt').write_text('keep')
        with self.assertRaisesRegex(ValueError,'already exists'):
            self.run_pipeline()
        self.assertEqual((self.out/'keep.txt').read_text(),'keep')

    def test_demo_is_not_a_real_target_success(self):
        b=demo_bundle()
        self.assertEqual(b['mode'],'synthetic_demo')
        self.assertFalse(summarise(b)['target_met'])
        self.assertEqual(summarise(b)['verified_emails'],0)
        self.assertEqual(len(b['sequences']),18)

    def test_source_quotes_require_exact_source_and_value(self):
        source={'url':'https://example.com','text':'Acme uses PostgreSQL','links':[],'fetched_at':now(),'kind':'page'}
        self.assertFalse(proof({'source_url':source['url'],'quote':'Acme uses MySQL'},[source]))
        self.assertFalse(proof({'source_url':'https://other.example.com','quote':source['text']},[source]))
        self.assertFalse(proof({'source_url':source['url'],'quote':source['text']},[source],literal='Oracle'))

    def test_public_url_network_boundary_and_budget(self):
        for url in ('http://127.0.0.1','http://169.254.169.254','https://[::1]','file:///etc/passwd','https://user:pass@example.com','https://example.com:8443'):
            with self.subTest(url=url),self.assertRaises(FetchError):
                validate_url(url)
        with patch('buyerfind.net.socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]),patch.dict('os.environ',{'BUYERFIND_TRUST_PROXY':'0'}):
            with self.assertRaisesRegex(FetchError,'private or reserved'):
                request('GET','https://example.com')
        provider=Providers(validate_config({'limits':{'model_calls':0}}))
        with self.assertRaises(BudgetExceeded):
            provider.ask('no call',{})
        self.assertEqual(provider.usage,{})

    def test_config_secrets_and_remote_plaintext_are_rejected(self):
        for value in ({'model':{'api_key':'secret'}},{'model':{'endpoint':'http://remote.example.com'}},{'limits':{'model_calls':-1}}):
            with self.assertRaises(ValueError):
                validate_config(value)

    def test_html_parser_keeps_observed_links_without_scripts(self):
        result=parse_page('https://example.com','<title>Acme</title><script>secret instruction</script><p>We build tools.</p><a href="/about">About</a>')
        self.assertNotIn('secret instruction',result['text'])
        self.assertIn('https://example.com/about',result['links'])

    def test_monitor_deduplicates_and_preserves_snapshot(self):
        b=self.run_pipeline()
        before=(self.out/'bundle.json').read_bytes()
        monitored=monitor_run(self.out,self.cfg,providers=ResearchBoundary())
        self.assertEqual(monitored['monitor_history'][-1]['new_signals'],0)
        snapshots=list(self.out.glob('before-monitor-*.json'))
        self.assertEqual(len(snapshots),1)
        self.assertEqual(snapshots[0].read_bytes(),before)


if __name__=='__main__':
    unittest.main()
