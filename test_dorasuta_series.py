import os,tempfile,unittest,sqlite3
from pathlib import Path
from unittest.mock import patch
import database,catalog,yuyutei,buyback,dorasuta,collectors
from source_fetch import init_control,UpdateError

STG='''検索件数：1 - 1件 / 1件
まゆり(UA57BT/STG-1-017)
SR★★★
STG1017
100,000円
数量：
'''
NIK='''検索件数：1 - 1件 / 1件
ラピ(PC02BT/NIK-001)
PcSR★★★
NIK001
20,000円
数量：
'''
JJK='''検索件数：1 - 1件 / 1件
五条 悟(UA02NC/JJK-2-004)
SP
JJK2004
400円
数量：
'''
class MultiDorasutaTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory();cls.old=database.DB_NAME;database.DB_NAME=Path(cls.temp.name)/'boot.db'
  os.environ.setdefault('CARD_TRACKER_SECRET_KEY','test');os.environ.setdefault('CARD_TRACKER_ADMIN_PASSWORD','test')
  from app import app
  cls.app=app;app.config['TESTING']=True
 @classmethod
 def tearDownClass(cls):database.DB_NAME=cls.old;cls.temp.cleanup()
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);database.DB_NAME=Path(self.tmp.name)/'test.db'
  init_control();yuyutei.init_tables();catalog.init_catalog();buyback.init_buyback();dorasuta.init_dorasuta();collectors.init_collectors();self.c=self.app.test_client();self.c.get('/')
  with self.c.session_transaction() as s:s['is_admin']=True;self.token=s['csrf_token']
 def test_all_categories_have_prices_and_filter(self):
  with database.get_connection() as db:
   self.assertEqual(len(dorasuta.coverage(db)),21)
   self.assertEqual(db.execute('SELECT COUNT(DISTINCT series_code) FROM dora_quotes').fetchone()[0],9)
   self.assertEqual(len(dorasuta.latest_quotes(db)),763)
  for code in ['CSM','JJK','KMY','HTR','BLC','SAO','NIK','CGH','STG']:
   r=self.c.get('/prices/compare?series='+code);self.assertEqual(r.status_code,200);self.assertNotIn('沒有符合條件的報價'.encode(),r.data)
  for pack in dorasuta.BY_PACK:self.assertEqual(self.c.get('/admin/dorasuta?pack='+pack).status_code,200,pack)
 def test_precious_sp_and_wrong_series_or_pack(self):
  self.assertEqual(dorasuta.parse_text(STG,pack='stg1')[0][0]['series_code'],'STG')
  self.assertEqual(dorasuta.parse_text(NIK,pack='nik2')[0][0]['rarity'],'PcSR★★★')
  self.assertEqual(dorasuta.parse_text(JJK,pack='jjk2')[0][0]['rarity'],'SP')
  with self.assertRaises(UpdateError):dorasuta.parse_text(STG,pack='csm1')
  with self.assertRaises(UpdateError):dorasuta.parse_text(NIK,pack='nik1')
 def test_import_updates_only_one_category_and_restart_preserves(self):
  with database.get_connection() as db:
   before={r['pack_code']:r['id'] for r in db.execute('SELECT * FROM dora_runs')}
  self.assertEqual(self.c.post('/admin/dorasuta',data={'csrf_token':self.token,'pack':'stg1','page_text':STG}).status_code,302)
  dorasuta.init_dorasuta()
  with database.get_connection() as db:
   coverage={s['pack']:s['latest'] for s in dorasuta.coverage(db)}
   self.assertEqual(coverage['stg1']['quote_count'],1)
   for pack,old in before.items():
    if pack!='stg1':self.assertEqual(coverage[pack]['id'],old)
   self.assertEqual(len(dorasuta.latest_quotes(db)),724)
  page=self.c.get('/prices/compare?series=STG&dora_pack=stg1');self.assertEqual(page.status_code,200);self.assertIn(b'100,000',page.data)
 def test_partial_update_and_failure_preserve_other_series(self):
  with database.get_connection() as db:count=db.execute('SELECT COUNT(*) FROM dora_runs').fetchone()[0]
  html='<html>'+STG.replace('1件 / 1件','1件 / 100件').replace('\n','<br>')+'</html>'
  with patch('dorasuta.fetch_page',return_value=html):
   with self.assertRaises(UpdateError):dorasuta.update_live('stg1')
  with database.get_connection() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM dora_runs').fetchone()[0],count)
 def test_legacy_single_category_schema_migration(self):
  old=database.DB_NAME;database.DB_NAME=Path(self.tmp.name)/'old.db'
  try:
   with database.get_connection() as db:
    db.execute('CREATE TABLE dora_runs(id INTEGER PRIMARY KEY,captured_at TEXT,quote_count INTEGER,expected_total INTEGER,method TEXT)')
    db.execute("INSERT INTO dora_runs VALUES(1,'2026-10-20T00:00:00+00:00',777,999,'user-import')");db.commit()
   dorasuta.init_dorasuta()
   with database.get_connection() as db:
    c=db.execute("SELECT * FROM dora_runs WHERE pack_code='csm1'").fetchall();self.assertEqual(len(c),1);self.assertEqual(c[0]['quote_count'],777)
    self.assertEqual(db.execute('SELECT COUNT(DISTINCT pack_code) FROM dora_runs').fetchone()[0],21)
  finally:database.DB_NAME=old
if __name__=='__main__':unittest.main()
