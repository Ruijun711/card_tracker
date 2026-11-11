import os,tempfile,unittest
from datetime import datetime,timezone
from pathlib import Path
from bs4 import BeautifulSoup
import database,catalog,yuyutei,buyback,dorasuta,collectors
from source_fetch import init_control
from recommendations import select_cards,combined_entries,recommendation_data
class CollectorRuleTests(unittest.TestCase):
 def test_no_total_or_series_limit(self):
  stamp=datetime.now(timezone.utc).isoformat();sales=[];offers=[]
  for i in range(30):
   p=dict(product_id='csm1:'+str(i),pack_code='csm1',card_name='卡'+str(i),card_number='CSM-1-'+str(i),rarity='SR',price_jpy=1000,stock='有貨',captured_at=stamp,image_url='',source_url='https://example.com')
   sales.append(p);offers.append(dict(p,price_jpy=700,active=1))
  stable,growth,_=select_cards(sales,offers,{},{});self.assertEqual(len(stable),30)
  self.assertEqual(len(combined_entries(stable,stable,[],'all')),30)
 def test_signatures_and_ap_are_distinct(self):
  base=dict(card_number='UA53BT/CSM-1-AP03★',rarity='AP',variant='AP／序號標示',card_name='ACTION POINT(AP/シリアル)レゼ')
  self.assertEqual(collectors.automatic_type(base),'official_ap')
  self.assertEqual(collectors.automatic_type(dict(base,card_number='UA01BT/CGH-1-AP03★')),'serial')
  ordinary=dict(base,card_number='UA53BT/CSM-1-AP03',variant='一般版本',card_name='APカード(レゼ)')
  self.assertEqual(collectors.automatic_type(ordinary),'none')
  self.assertEqual(collectors.automatic_type(dict(ordinary,card_name='箔押しサイン')),'printed')
  self.assertEqual(collectors.automatic_type(dict(ordinary,card_name='直筆風プリント')),'printed')
  self.assertEqual(collectors.automatic_type(dict(ordinary,card_name='直筆サイン')),'handwritten')
 def test_required_evidence_and_urls(self):
  base=dict(classification='handwritten',visibility='auto')
  with self.assertRaises(ValueError):collectors.validate_metadata(base)
  base.update(classification='rare',edition_size='100')
  with self.assertRaises(ValueError):collectors.validate_metadata(base)
  base.update(edition_url='javascript:alert(1)')
  with self.assertRaises(ValueError):collectors.validate_metadata(base)
  base.update(edition_url='https://example.com/edition',sale_price='1000')
  with self.assertRaises(ValueError):collectors.validate_metadata(base)
class CollectorPageTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory();cls.old=database.DB_NAME;database.DB_NAME=Path(cls.temp.name)/'boot.db'
  os.environ.setdefault('CARD_TRACKER_SECRET_KEY','test-only');os.environ.setdefault('CARD_TRACKER_ADMIN_PASSWORD','test-only')
  from app import app
  cls.app=app;app.config['TESTING']=True
 @classmethod
 def tearDownClass(cls):database.DB_NAME=cls.old;cls.temp.cleanup()
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);database.DB_NAME=Path(self.tmp.name)/'test.db'
  init_control();yuyutei.init_tables();catalog.init_catalog();buyback.init_buyback();dorasuta.init_dorasuta();collectors.init_collectors();self.c=self.app.test_client();self.c.get('/')
  with self.c.session_transaction() as s:s['is_admin']=True;self.token=s['csrf_token']
 def test_all_pages_collector_sources_and_counts(self):
  with self.app.test_request_context():
   stable,growth,_=recommendation_data();collection=collectors.collection_candidates();all_rows=combined_entries(stable,growth,collection,'all')
   self.assertGreater(len(all_rows),24)
   official=[c for c in collection if c['kind']=='official_ap'];self.assertEqual(len(official),3)
   reze=next(c for c in official if 'AP03★' in c['card_number']);self.assertIsNone(reze['edition_size']);self.assertTrue(any('缺貨' in g for g in reze['missing']))
  page1=self.c.get('/recommendations');page2=self.c.get('/recommendations?page=2')
  self.assertEqual(page1.status_code,200);self.assertEqual(page2.status_code,200)
  self.assertEqual(len(BeautifulSoup(page1.data,'html.parser').select('article.recommend-card')),24)
  self.assertNotEqual(page1.data,page2.data)
  self.assertIn('全部符合'.encode(),self.c.get('/recommendations?mode=collectors').data.replace('所有符合'.encode(),'全部符合'.encode()))
  self.assertNotIn('每類最多 12'.encode(),page1.data)
  for path in ['/recommendations?mode=collectors&series=CSM','/recommendations?mode=growth','/admin/collectors','/admin/collectors?series=CSM']:
   self.assertEqual(self.c.get(path).status_code,200,path)
 def test_metadata_permissions_edit_and_exclude(self):
  with self.app.test_request_context():card=next(c for c in collectors.collection_candidates() if c['card_number'].endswith('AP03★'))
  url='/admin/collectors/'+card['collector_id'];guest=self.app.test_client()
  self.assertEqual(guest.get(url).status_code,302);self.assertEqual(guest.post(url).status_code,400)
  bad=self.c.post(url,data={'csrf_token':self.token,'classification':'handwritten','visibility':'auto'},follow_redirects=True);self.assertIn('請附上來源'.encode(),bad.data)
  good=dict(csrf_token=self.token,classification='auto',visibility='auto',evidence_note='<script>研究說明</script>',condition_note='卡況待實物核對')
  self.assertEqual(self.c.post(url,data=good).status_code,302)
  r=self.c.get('/recommendations?mode=collectors&series=CSM');self.assertIn(b'&lt;script&gt;',r.data);self.assertNotIn(b'<script>\xe7\xa0\x94',r.data)
  good['visibility']='exclude';self.c.post(url,data=good)
  with self.app.test_request_context():self.assertNotIn(card['collector_id'],[c['collector_id'] for c in collectors.collection_candidates()])
  self.assertEqual(self.c.get(url).status_code,200)
  good['visibility']='auto';self.c.post(url,data=good)
  with self.app.test_request_context():self.assertIn(card['collector_id'],[c['collector_id'] for c in collectors.collection_candidates()])
if __name__=='__main__':unittest.main()
