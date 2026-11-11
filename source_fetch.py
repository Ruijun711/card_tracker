import socket,time
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError,URLError
from database import get_connection
class UpdateError(Exception):pass
class NoRedirect(HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise UpdateError('來源頁面轉址，已停止更新。')
def fetch_page(url):
 try:
  with build_opener(NoRedirect()).open(Request(url,headers={'User-Agent':'CardTracker/2.0 (manual update)','Accept':'text/html'}),timeout=20) as response:
   if response.headers.get_content_type()!='text/html':raise UpdateError('來源未回傳網頁。')
   raw=response.read(5_000_001)
   if len(raw)>5_000_000:raise UpdateError('來源頁面過大，已停止更新。')
   return raw.decode('utf-8')
 except HTTPError as e:raise UpdateError(f'來源回應 HTTP {e.code}；未更新，原有資料已保留。') from e
 except (URLError,TimeoutError,socket.timeout,UnicodeError) as e:raise UpdateError('來源連線或讀取失敗；原有資料已保留。') from e
def init_control():
 with get_connection() as db:db.execute('CREATE TABLE IF NOT EXISTS sync_control (source_key TEXT PRIMARY KEY,last_attempt REAL NOT NULL)');db.commit()
def reserve(key):
 with get_connection() as db:
  db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT last_attempt FROM sync_control WHERE source_key=?',(key,)).fetchone();now=time.time()
  if row and now-row[0]<60:raise UpdateError('同一分類請等待至少 60 秒再更新。')
  db.execute('INSERT INTO sync_control VALUES(?,?) ON CONFLICT(source_key) DO UPDATE SET last_attempt=excluded.last_attempt',(key,now));db.commit()
