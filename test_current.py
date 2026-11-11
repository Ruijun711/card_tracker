"""Current feature/regression tests. Never modifies the user's database."""
import io,os,tempfile,sqlite3,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import database,catalog,yuyutei
from source_fetch import init_control,UpdateError

HTML='''<html><div class="card-product {sold}"><a href="https://yuyu-tei.jp/sell/ua/card/{pack}/{pid}"><div class="product-img"><img src="https://card.yuyu-tei.jp/ua/100_140/{pack}/{pid}.jpg" alt="{number} SR★ 測試卡(異圖)"></div><h4>測試卡(異圖)</h4></a><span class="border">{number}</span><strong>{price} 円</strong><label class="cart_sell_zaiko">在庫 : {stock}</label></div></html>'''
def fixture(pack='csm1',price='2,480',sold='',stock='1 点'):
 return HTML.format(pack=pack,pid='10030',number='UA53BT/CSM-1-024' if pack=='csm1' else 'UA02BT/JJK-1-024',price=price,sold=sold,stock=stock)
class CurrentTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.root=tempfile.TemporaryDirectory();cls.old=database.DB_NAME;database.DB_NAME=Path(cls.root.name)/'boot.db'
  os.environ.setdefault('CARD_TRACKER_SECRET_KEY','test-only');os.environ.setdefault('CARD_TRACKER_ADMIN_PASSWORD','test-only')
  from app import app
  cls.app=app;app.config['TESTING']=True;cls.old_static=app.static_folder
 @classmethod
 def tearDownClass(cls):
  database.DB_NAME=cls.old;cls.app.static_folder=cls.old_static;cls.root.cleanup()
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  database.DB_NAME=Path(self.temp.name)/'test.db';self.app.static_folder=str(Path(self.temp.name)/'static')
  init_control();yuyutei.init_tables();catalog.init_catalog();
  from buyback import init_buyback
  init_buyback();
  from dorasuta import init_dorasuta
  init_dorasuta();
  from collectors import init_collectors
  init_collectors();self.c=self.app.test_client();self.c.get('/')
  with self.c.session_transaction() as s:s['is_admin']=True;self.token=s['csrf_token']
 def test_series_pages_and_removed_routes(self):
  with database.get_connection() as db:
   self.assertEqual(db.execute('SELECT COUNT(DISTINCT series_code) FROM encyclopedia').fetchone()[0],9)
   self.assertEqual(db.execute('SELECT COUNT(*) FROM encyclopedia').fetchone()[0],2409)
   self.assertEqual(len(yuyutei.latest_products(db)),2372)
  for path in ['/','/?series=JJK','/?series=NIK&page=2','/?series=STG','/yuyutei?series=STG&pack=stg1','/yuyutei','/yuyutei?series=JJK&pack=jjk1','/admin','/admin/catalog?series=SAO','/admin/card-media?scope=yuyu&state=missing','/admin/card-media?scope=catalog&series=BLC','/admin/login']:
   r=self.c.get(path);self.assertEqual(r.status_code,200,path);self.assertNotIn('遊遊亭'.encode(),r.data)
  r=self.c.get('/?series=JJK');self.assertIn(b'JJK-1-',r.data);self.assertNotIn(b'CSM-1-',r.data);self.assertNotIn(b'NT$',r.data)
  for path in ['/market','/admin/images','/import','/card/1/analysis','/edit/1']:
   self.assertEqual(self.c.get(path).status_code,404,path)
  for path in ['/add','/delete/1','/import/confirm']:
   self.assertEqual(self.c.post(path,data={'csrf_token':self.token}).status_code,404,path)
  self.assertEqual(self.c.get('/?series=UNKNOWN').status_code,400)
  self.assertEqual(self.c.post('/admin/yuyutei/update',data={'csrf_token':self.token,'pack':''}).status_code,400)
 def test_pack_identity_and_atomic_failure(self):
  for pack in ['csm1','jjk1']:
   with patch('yuyutei.fetch_html',return_value=fixture(pack)):self.assertEqual(yuyutei.update_prices(pack),1)
  with database.get_connection() as db:
   rows=yuyutei.latest_products(db);ids={r['product_id'] for r in rows};self.assertIn('csm1:10030',ids);self.assertIn('jjk1:10030',ids)
   self.assertGreater(len(rows),2)
  with patch('yuyutei.fetch_html',return_value=fixture()):
   with self.assertRaises(UpdateError):yuyutei.update_prices('csm1')
  with database.get_connection() as db:
   db.execute('DELETE FROM sync_control');db.commit();before=db.execute('SELECT COUNT(*) FROM yuyu_runs').fetchone()[0]
  with patch('yuyutei.fetch_html',return_value='<html>Blocked</html>'):
   with self.assertRaises(UpdateError):yuyutei.update_prices('csm1')
  with database.get_connection() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM yuyu_runs').fetchone()[0],before)
  p=yuyutei.parse_products(fixture(sold='sold-out',stock='×'))[0];self.assertEqual(p['price_jpy'],2480);self.assertEqual(p['stock'],'缺貨')
  with self.assertRaises(UpdateError):yuyutei.parse_products(fixture(price='0'))
 def test_auth_csrf_and_uploads(self):
  guest=self.app.test_client();guest.get('/')
  self.assertEqual(guest.get('/admin/card-media').status_code,302)
  self.assertEqual(guest.post('/admin/yuyutei/update').status_code,400)
  with guest.session_transaction() as s:token=s['csrf_token']
  with patch('yuyutei.fetch_html') as fetch:
   guest.post('/admin/yuyutei/update',data={'csrf_token':token,'pack':'csm1'});fetch.assert_not_called()
  def upload(color):
   raw=io.BytesIO();Image.new('RGB',(80,120),color).save(raw,'PNG');raw.seek(0)
   return self.c.post('/admin/card-media',data={'csrf_token':self.token,'scope':'yuyu','item_id':'csm1:10030','image':(raw,'card.png')},content_type='multipart/form-data')
  self.assertEqual(upload('red').status_code,302)
  with database.get_connection() as db:first=db.execute("SELECT image_path FROM image_overrides WHERE item_id='csm1:10030'").fetchone()[0]
  self.assertEqual(upload('blue').status_code,302)
  with database.get_connection() as db:second=db.execute("SELECT image_path FROM image_overrides WHERE item_id='csm1:10030'").fetchone()[0]
  self.assertNotEqual(first,second)
  with patch('yuyutei.fetch_html',return_value=fixture()):yuyutei.update_prices('csm1')
  self.assertIn(second.encode(),self.c.get('/yuyutei?pack=csm1').data)
  r=self.c.post('/admin/card-media',data={'csrf_token':self.token,'scope':'yuyu','item_id':'csm1:10030','image':(io.BytesIO(b'not an image'),'fake.png')},content_type='multipart/form-data',follow_redirects=True)
  self.assertIn('圖片無法辨識'.encode(),r.data)
 def test_effect_edit_and_sync_preserves_override(self):
  with database.get_connection() as db:card=dict(db.execute("SELECT * FROM encyclopedia WHERE series_code='CSM' ORDER BY card_id LIMIT 1").fetchone())
  key=card['card_id'];url='/admin/catalog/'+key
  self.assertEqual(self.c.get(url).status_code,200)
  r=self.c.post(url,data={'csrf_token':self.token,'card_name':'自訂名稱','effect_text':'<script>alert(1)</script>','trigger_text':'抽一張卡'},follow_redirects=True)
  self.assertEqual(r.status_code,200)
  r=self.c.get('/?keyword=自訂名稱');self.assertIn(b'&lt;script&gt;',r.data);self.assertNotIn(b'<script>alert(1)</script>',r.data)
  with patch('catalog.fetch_page',return_value='<html></html>'),patch('catalog.parse_catalog',return_value=[card]):catalog.sync_catalog('CSM')
  self.assertIn('自訂名稱'.encode(),self.c.get('/?keyword=自訂名稱').data)
  self.c.post(url,data={'csrf_token':self.token,'card_name':'','effect_text':'','trigger_text':''})
  self.assertIn(card['card_name'].encode(),self.c.get('/?keyword='+card['card_number']).data)
 def test_legacy_migration_and_override(self):
  old_path=database.DB_NAME;database.DB_NAME=Path(self.temp.name)/'legacy.db'
  try:
   with database.get_connection() as db:
    db.executescript("""CREATE TABLE yuyu_runs(id INTEGER PRIMARY KEY,captured_at TEXT,product_count INTEGER);
     INSERT INTO yuyu_runs VALUES(1,'2026-10-20T00:00:00+00:00',1);
     CREATE TABLE yuyu_prices(run_id INTEGER,product_id TEXT,card_number TEXT,card_name TEXT,rarity TEXT,price_jpy INTEGER,stock TEXT,source_url TEXT,PRIMARY KEY(run_id,product_id));
     INSERT INTO yuyu_prices VALUES(1,'10030','CSM-1-024','old','SR',888,'有貨','https://yuyu-tei.jp/sell/ua/card/csm1/10030');
     CREATE TABLE image_overrides(scope TEXT,item_id TEXT,image_path TEXT,PRIMARY KEY(scope,item_id));
     INSERT INTO image_overrides VALUES('yuyu','10030','card_images/user.webp');
     CREATE TABLE card_prices(id INTEGER PRIMARY KEY,price INTEGER);INSERT INTO card_prices VALUES(1,456);""")
   yuyutei.init_tables();catalog.init_catalog();yuyutei.init_tables()
   with database.get_connection() as db:
    self.assertEqual(db.execute("SELECT price_jpy FROM yuyu_prices WHERE product_id='csm1:10030'").fetchone()[0],888)
    self.assertEqual(db.execute("SELECT image_path FROM image_overrides WHERE item_id='csm1:10030'").fetchone()[0],'card_images/user.webp')
    self.assertEqual(db.execute('SELECT price FROM card_prices').fetchone()[0],456)
    self.assertEqual(db.execute("SELECT COUNT(*) FROM yuyu_runs WHERE pack_code='csm1'").fetchone()[0],1)
  finally:database.DB_NAME=old_path
if __name__=='__main__':unittest.main()
