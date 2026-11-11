import io,os,tempfile,unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
import database,yuyutei,catalog,buyback
from source_fetch import init_control,UpdateError
from recommendations import select_cards

HTML='''<html><div class="card-product priceup"><a href="https://yuyu-tei.jp/buy/ua/card/csm1/10030"><div class="product-img"><img src="https://card.yuyu-tei.jp/ua/100_140/csm1/10030.jpg" alt="UA53BT/CSM-1-024 SR★ 測試卡"></div><h4>測試卡</h4></a><span class="border">UA53BT/CSM-1-024</span><strong>{price} 円</strong><small><del>999 円</del></small><a class="cart_sell_in {disabled}">買取リストへ</a></div></html>'''
def fixture(price='600',disabled=''):return HTML.format(price=price,disabled=disabled)
class RankingTests(unittest.TestCase):
 def setUp(self):
  self.now=datetime.now(timezone.utc);stamp=self.now.isoformat()
  self.sale=dict(product_id='csm1:10030',pack_code='csm1',card_name='測試卡',card_number='UA53BT/CSM-1-024',rarity='SR★',price_jpy=1000,stock='有貨',captured_at=stamp,image_url='',source_url='https://yuyu-tei.jp/sell/ua/card/csm1/10030')
  self.bid=dict(self.sale,price_jpy=700,active=1,source_url='https://yuyu-tei.jp/buy/ua/card/csm1/10030')
 def choose(self,sale=None,bid=None,sh=None,bh=None):
  return select_cards([sale or self.sale],[bid or self.bid],{'csm1:10030':sh or []},{'csm1:10030':bh or []},self.now)
 def history(self,prices,offsets):return [dict(price_jpy=p,active=1,captured_at=(self.now-timedelta(days=d)).isoformat()) for p,d in zip(prices,offsets)]
 def test_initial_screen_is_not_growth(self):
  stable,growth,matched=self.choose();self.assertEqual(len(stable),1);self.assertEqual(stable[0]['confidence'],'初步觀察');self.assertEqual(growth,[])
 def test_mismatch_stale_out_of_stock_and_suspension(self):
  cases=[(dict(self.sale,stock='缺貨'),self.bid),(self.sale,dict(self.bid,active=0)),(self.sale,dict(self.bid,price_jpy=1200)),(self.sale,dict(self.bid,card_name='異圖版不同')),(self.sale,dict(self.bid,captured_at=(self.now-timedelta(days=8)).isoformat()))]
  for sale,bid in cases:self.assertEqual(self.choose(sale,bid)[0],[])
 def test_multi_day_growth_and_same_day_duplicates(self):
  sh=self.history([1000,1100,1200],[8,4,0]);bh=self.history([600,660,720],[8,4,0])
  stable,growth,_=self.choose(dict(self.sale,price_jpy=1200),dict(self.bid,price_jpy=720),sh,bh);self.assertEqual(len(growth),1)
  sh=self.history([1000,1100,1200],[0,0,0]);bh=self.history([600,660,720],[0,0,0]);self.assertEqual(self.choose(sh=sh,bh=bh)[1],[])
 def test_historical_stability_and_drawdown(self):
  sh=self.history([1000,1000,1000],[15,7,0]);stable,_,_=self.choose(sh=sh);self.assertEqual(stable[0]['confidence'],'有歷史支持')
  sh=self.history([2000,1800,1000],[15,7,0]);self.assertEqual(self.choose(sh=sh)[0],[])
class BuybackTests(unittest.TestCase):
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
  init_control();yuyutei.init_tables();catalog.init_catalog();buyback.init_buyback();
  from dorasuta import init_dorasuta
  init_dorasuta();
  from collectors import init_collectors
  init_collectors();self.c=self.app.test_client();self.c.get('/')
  with self.c.session_transaction() as s:s['is_admin']=True;self.token=s['csrf_token']
 def test_parsing_current_price_not_deleted_old_price(self):
  p=buyback.parse_buyback(fixture(),'csm1')[0];self.assertEqual(p['price_jpy'],600);self.assertEqual(p['active'],1)
  self.assertEqual(buyback.parse_buyback(fixture(disabled='disabled'),'csm1')[0]['active'],0)
  self.assertEqual(buyback.parse_buyback(fixture(price='0'),'csm1')[0]['active'],0)
  with self.assertRaises(UpdateError):buyback.parse_buyback('<html>blocked</html>','csm1')
 def test_update_separation_failure_and_latest_suspension(self):
  with database.get_connection() as db:before=db.execute('SELECT COUNT(*) FROM yuyu_runs').fetchone()[0]
  with patch('buyback.fetch_page',return_value=fixture()):self.assertEqual(buyback.update_offers('csm1'),1)
  self.assertIn('測試卡'.encode(),self.c.get('/buyback?pack=csm1').data)
  with self.assertRaises(UpdateError):buyback.update_offers('csm1')
  with database.get_connection() as db:db.execute('DELETE FROM sync_control');db.commit();runs=db.execute('SELECT COUNT(*) FROM buy_runs').fetchone()[0]
  with patch('buyback.fetch_page',return_value='<html>invalid</html>'):
   with self.assertRaises(UpdateError):buyback.update_offers('csm1')
  with database.get_connection() as db:
   self.assertEqual(db.execute('SELECT COUNT(*) FROM buy_runs').fetchone()[0],runs);self.assertEqual(db.execute('SELECT COUNT(*) FROM yuyu_runs').fetchone()[0],before);db.execute('DELETE FROM sync_control');db.commit()
  with patch('buyback.fetch_page',return_value=fixture(disabled='disabled')):buyback.update_offers('csm1')
  self.assertNotIn('測試卡'.encode(),self.c.get('/buyback?pack=csm1').data)
  self.assertIn('暫停收購'.encode(),self.c.get('/buyback?pack=csm1&product=csm1:10030').data)
 def test_auth_csrf_filters_and_pages(self):
  guest=self.app.test_client();guest.get('/')
  self.assertEqual(guest.post('/admin/buyback/update').status_code,400)
  with guest.session_transaction() as s:token=s['csrf_token']
  with patch('buyback.fetch_page') as fetch:
   self.assertEqual(guest.post('/admin/buyback/update',data={'csrf_token':token,'pack':'csm1'}).status_code,302);fetch.assert_not_called()
  for path in ['/buyback','/buyback?series=STG&pack=stg1','/buyback?keyword=NOTFOUND','/recommendations','/recommendations?series=STG&mode=growth','/admin','/admin/card-media?scope=yuyu']:
   self.assertEqual(self.c.get(path).status_code,200,path)
  self.assertEqual(self.c.get('/buyback?pack=untrusted').status_code,400)
  self.assertIn(b'data-jpy=',self.c.get('/buyback').data)
if __name__=='__main__':unittest.main()
