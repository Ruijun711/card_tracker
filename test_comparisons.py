import io,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime,timezone
import database,catalog,yuyutei,buyback,dorasuta
from comparisons import grouped_prices
from source_fetch import init_control,UpdateError

TEXT='''検索件数：1 - 2件 / 145件
SOLDOUT
ACTION POINT(AP/シリアル)(UA53BT/CSM-1-AP03★)〖レゼ〗
AP
CSM1AP03
2,500,000円
レゼ(UA53BT/CSM-1-031)
SR★
CSM1031
2,500円
数量：
'''
class ComparisonTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory();cls.old=database.DB_NAME;database.DB_NAME=Path(cls.temp.name)/'bootstrap.db'
  os.environ.setdefault('CARD_TRACKER_SECRET_KEY','test-only');os.environ.setdefault('CARD_TRACKER_ADMIN_PASSWORD','test-only')
  from app import app
  cls.app=app;app.config['TESTING']=True
 @classmethod
 def tearDownClass(cls):database.DB_NAME=cls.old;cls.temp.cleanup()
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);database.DB_NAME=Path(self.tmp.name)/'test.db'
  init_control();yuyutei.init_tables();catalog.init_catalog();buyback.init_buyback();dorasuta.init_dorasuta();
  from collectors import init_collectors
  init_collectors();self.c=self.app.test_client();self.c.get('/')
  with self.c.session_transaction() as s:s['is_admin']=True;self.token=s['csrf_token']
 def test_variants_conditions_and_currency(self):
  items,total=dorasuta.parse_text(TEXT);self.assertEqual(total,145);self.assertEqual(len(items),2);self.assertEqual(items[0]['variant'],'AP／序號標示');self.assertEqual(items[0]['stock'],'缺貨')
  self.assertEqual(items[1]['stock'],'有貨')
  sale=dict(product_id='csm1:10042',pack_code='csm1',card_number='UA53BT/CSM-1-031',rarity='SR★',card_name='レゼ(パラレル)',price_jpy=7980,stock='有貨',source_url='https://yuyu-tei.jp/sell/ua/card/csm1/10042',image_url='',captured_at=datetime.now(timezone.utc).isoformat())
  quotes=[dict(i,series_code='CSM',captured_at=sale['captured_at'],method='manual') for i in items]
  groups=grouped_prices([sale],quotes);self.assertEqual(len(groups),2)
  regular=next(g for g in groups if g['rarity']=='SR★');self.assertEqual(sorted(q['price_jpy'] for q in regular['quotes']),[2500,7980])
  played=dict(quotes[1],condition='プレイ用');self.assertEqual(len(grouped_prices([sale],[played])),2)
  self.assertEqual(dorasuta.identity('UA53BT/CSM-1-AP03','-','一般標示','一般版本'),dorasuta.identity('UA53BT/CSM-1-AP03','AP','一般標示','一般版本'))
 def test_challenge_partial_import_and_atomic_failure(self):
  with self.assertRaises(UpdateError):dorasuta.parse_html('<html><title>Just a moment...</title></html>')
  html='<html><body>'+TEXT.replace('\n','<br>')+'</body></html>'
  self.assertEqual(len(dorasuta.parse_html(html)[0]),2)
  with database.get_connection() as db:before=db.execute('SELECT COUNT(*) FROM dora_runs').fetchone()[0]
  with patch('dorasuta.fetch_page',return_value=html):
   with self.assertRaises(UpdateError):dorasuta.update_live()
  with database.get_connection() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM dora_runs').fetchone()[0],before)
  r=self.c.post('/admin/dorasuta',data={'csrf_token':self.token,'page_text':TEXT},follow_redirects=True);self.assertEqual(r.status_code,200)
  with database.get_connection() as db:self.assertEqual(db.execute('SELECT quote_count FROM dora_runs ORDER BY id DESC LIMIT 1').fetchone()[0],2)
  r=self.c.get('/prices/compare?view=dorasuta&keyword=CSM-1-031');self.assertEqual(r.status_code,200);self.assertIn('最低價'.encode(),r.data);self.assertIn(b'2,500',r.data);self.assertIn('部分'.encode(),r.data)
  self.c.post('/admin/dorasuta',data={'csrf_token':self.token,'page_text':'invalid'},follow_redirects=True)
  with database.get_connection() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM dora_runs').fetchone()[0],before+1)
 def test_html_upload_auth_pages_and_seed_preservation(self):
  guest=self.app.test_client();guest.get('/')
  self.assertEqual(guest.get('/admin/dorasuta').status_code,302);self.assertEqual(guest.post('/admin/dorasuta').status_code,400)
  for path in ['/prices/compare','/prices/compare?view=matched','/prices/compare?stock=available','/admin/dorasuta','/admin/card-media?scope=dorasuta']:
   self.assertEqual(self.c.get(path).status_code,200,path)
  data={'csrf_token':self.token,'pages':(io.BytesIO(('<html>'+TEXT.replace('\n','<br>')+'</html>').encode()),'page.html')}
  self.assertEqual(self.c.post('/admin/dorasuta',data=data,content_type='multipart/form-data').status_code,302)
  dorasuta.init_dorasuta()
  with database.get_connection() as db:self.assertEqual(db.execute('SELECT quote_count FROM dora_runs ORDER BY id DESC LIMIT 1').fetchone()[0],2)
if __name__=='__main__':unittest.main()
