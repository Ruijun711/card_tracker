"""Per-category Dorasuta quote snapshots with explicit provenance and coverage."""
import hashlib,json,re,unicodedata
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import urljoin,urlparse,parse_qs
from bs4 import BeautifulSoup
from flask import Blueprint,request,session,redirect,url_for,render_template,flash,abort,current_app
from database import get_connection
from source_fetch import UpdateError,fetch_page,reserve
SOURCE='https://dorasuta.jp/union-arena/product-list?sid=13511'
bp=Blueprint('dorasuta',__name__)
NUMBER=r'(?:UA\d{2}(?:BT|ST|EX|DC|NC|PC)|EX\d{2}BT|PC\d{2}BT|UAPR|URPR|UAPB|PCPR)/[A-Z0-9]+-[A-Z0-9-]+[★☆]*'
SOURCES=json.loads(Path(__file__).with_name('dorasuta_sources.json').read_text(encoding='utf-8'))
BY_PACK={r['pack']:r for r in SOURCES}
def source_info(pack):
 if pack not in BY_PACK:raise UpdateError('未知 Dorasuta 分類。')
 return BY_PACK[pack]


def normalized(value):return unicodedata.normalize('NFKC',value).strip()
def edition(name,number):
 text=normalized(name).lower()
 if '直筆' in text:return '親筆標示'
 if 'シリアル' in text or ('-AP' in number and '★' in number):return 'AP／序號標示'
 if 'winner' in text or '優勝' in text:return 'WINNER'
 if 'サイン' in text:return '簽名標示待核對'
 return '一般版本'
def identity(number,rarity,condition,variant):
 rarity='AP' if '-AP' in number and rarity in ('-','AP') else rarity
 return (normalized(number),normalized(rarity),condition,variant)
def safe_source_url(value,pack='csm1'):
 u=urlparse(value)
 return value if u.scheme=='https' and u.hostname=='dorasuta.jp' and u.path in ('/union-arena/product','/union-arena/product-list') and not u.username else source_info(pack)['url']

def plain_html(html,pack='csm1'):
 source_info(pack)
 if '</html>' not in html.lower():raise UpdateError('網頁不完整，未匯入。')
 if any(word in html.lower() for word in ('cf-chl-','challenges.cloudflare.com','just a moment...')):raise UpdateError('Dorasuta 回傳網站驗證頁，無法自動更新。可在瀏覽器正常開啟後，匯入商品頁 HTML 或貼上頁面文字。')
 soup=BeautifulSoup(html,'html.parser');links={}
 for a in soup.select('a[href]'):
  title=a.get_text(' ',strip=True)
  if re.search(NUMBER,title):links[normalized(title)]=safe_source_url(urljoin(source_info(pack)['url'],a['href']),pack)
 for n in soup.select('script,style,noscript,iframe'):n.decompose()
 for img in soup.select('img'):
  alt=img.get('alt','')
  if any(w in alt for w in ('SOLDOUT','プレイ用','実画像','特価')):img.replace_with('\n'+alt+'\n')
  else:img.decompose()
 return soup.get_text('\n',strip=True),links

def parse_text(text,source_urls=None,pack='csm1'):
 source=source_info(pack)
 source_urls=source_urls or {};lines=[]
 for raw in text.splitlines():
  raw=re.sub(r'^L\d+:\s*','',raw).strip()
  if 'Image:' in raw:
   # Preserve condition/promotion badges, but ignore duplicate product-image captions.
   if not any(w in raw for w in ('プレイ用','実画像','特価','SOLDOUT')):continue
  raw=re.sub(r'【\d+†([^】]+)】',r'\1',raw)
  raw=normalized(raw).lstrip('* ').strip()
  if raw:lines.append(raw)
 text='\n'.join(lines)
 if source['series_code'] not in text:raise UpdateError('沒有找到所選作品的卡片資料。')
 totals=re.findall(r'検索件数[：:]\s*[\d,]+\s*[-－–]\s*[\d,]+\s*件\s*[/／]\s*([\d,]+)\s*件',text)
 total=int(totals[-1].replace(',','')) if totals else None
 results={}
 for i,line in enumerate(lines):
  match=re.search(r'\(('+NUMBER+r')\)',line)
  if not match or 'Image:' in line:continue
  title=line.strip();number=match.group(1)
  if number.split('/')[1].split('-')[0]!=source['series_code']:raise UpdateError('頁面作品與所選 Dorasuta 分類不符，未匯入。')
  if source.get('card_prefixes') and number.split('/')[0] not in source['card_prefixes']:raise UpdateError('卡號前綴與所選商品分類不符，請確認 Vol.／預組／特選卡分類。')
  if any(w in title for w in ('画像を','alt=')):continue
  following=[]
  for following_line in lines[i+1:i+14]:
   if re.search(r'\('+NUMBER+r'\)',following_line):break
   following.append(following_line)
  rarity=next((v for v in following[:5] if re.fullmatch(r'(?:(?:PR-)?(?:Pc)?(?:SR|R|C|U)|UR|AP|SP|PR|OBC)(?:★){0,3}(?:/プロモ)?',v)),None)
  price=next((re.fullmatch(r'([\d,]+)\s*円',v) for v in following if re.fullmatch(r'([\d,]+)\s*円',v)),None)
  if not rarity or not price:continue
  amount=int(price.group(1).replace(',',''))
  if not 0<amount<=100_000_000:raise UpdateError('偵測到異常價格，未匯入。')
  previous_lines=lines[max(0,i-3):i]
  headings=[j for j,v in enumerate(previous_lines) if v.startswith('#')]
  if headings:previous_lines=previous_lines[headings[-1]+1:]
  previous=' '.join(previous_lines)
  condition='プレイ用' if 'プレイ用' in title else '一般標示'
  if '実画像' in previous:condition='實拍／個別卡況'
  promotion='特價' if '特価' in previous else ''
  stock='缺貨' if 'SOLDOUT' in previous.upper() else '有貨' if any('数量' in v for v in following) else '未確認'
  variant=edition(title,number)
  key=identity(number,rarity,condition,variant)+(promotion,)
  quote_id=hashlib.sha256('|'.join(key).encode()).hexdigest()[:24]
  item=dict(quote_id=quote_id,card_number=number,card_name=title,rarity=rarity,condition=condition,variant=variant,promotion=promotion,price_jpy=amount,stock=stock,source_url=source_urls.get(title,source['url']),image_url='',series_code=source['series_code'],pack_code=pack)
  old=results.get(quote_id)
  if old and old['price_jpy']!=amount:raise UpdateError('同版本／卡況出現多個未能區分的價格，請分開匯入或核對來源。')
  if not old or (old['stock']=='未確認' and stock!='未確認'):results[quote_id]=item
 if not results:raise UpdateError('找不到可辨識的 Dorasuta 商品與價格；請提供商品列表完整文字或 HTML。')
 return list(results.values()),total

def parse_html(html,pack='csm1'):
 text,links=plain_html(html,pack);return parse_text(text,links,pack)

def save_snapshot(db,items,total,method,pack='csm1',captured=None):
 source=source_info(pack)
 if any(p['card_number'].split('/')[1].split('-')[0]!=source['series_code'] for p in items):raise UpdateError('報價作品與分類不符。')
 stamp=captured or datetime.now(timezone.utc).isoformat(timespec='seconds')
 run=db.execute('INSERT INTO dora_runs(captured_at,quote_count,expected_total,method,pack_code) VALUES(?,?,?,?,?)',(stamp,len(items),total,method,pack)).lastrowid
 db.executemany('INSERT INTO dora_quotes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',[(run,p['quote_id'],p['card_number'],p['card_name'],p['rarity'],p['condition'],p['variant'],p['promotion'],p['price_jpy'],p['stock'],p['source_url'],p.get('image_url',''),source['series_code']) for p in items])
 return run

def init_dorasuta():
 with get_connection() as db:
  db.executescript("""CREATE TABLE IF NOT EXISTS dora_runs(id INTEGER PRIMARY KEY,captured_at TEXT NOT NULL,quote_count INTEGER NOT NULL,expected_total INTEGER,method TEXT NOT NULL,pack_code TEXT NOT NULL DEFAULT 'csm1');
   CREATE TABLE IF NOT EXISTS dora_quotes(run_id INTEGER NOT NULL,quote_id TEXT NOT NULL,card_number TEXT NOT NULL,card_name TEXT NOT NULL,rarity TEXT NOT NULL,condition TEXT NOT NULL,variant TEXT NOT NULL,promotion TEXT NOT NULL,price_jpy INTEGER NOT NULL CHECK(price_jpy>0),stock TEXT NOT NULL,source_url TEXT NOT NULL,image_url TEXT NOT NULL,series_code TEXT NOT NULL,PRIMARY KEY(run_id,quote_id));""")
  if 'pack_code' not in {r['name'] for r in db.execute('PRAGMA table_info(dora_runs)')}:db.execute("ALTER TABLE dora_runs ADD COLUMN pack_code TEXT NOT NULL DEFAULT 'csm1'")
  db.execute('CREATE INDEX IF NOT EXISTS dora_category ON dora_runs(pack_code,id)')
  seed=Path(__file__).with_name('dorasuta_seed.json')
  if seed.exists():
   batches=json.loads(seed.read_text(encoding='utf-8'));batches=batches if isinstance(batches,list) else [dict(batches,pack='csm1')]
   for batch in batches:
    pack=batch.get('pack','csm1');source_info(pack)
    if not db.execute('SELECT 1 FROM dora_runs WHERE pack_code=? LIMIT 1',(pack,)).fetchone():
     save_snapshot(db,batch['products'],batch.get('total'),batch.get('method','網頁檢索快照（可能非即時；分頁覆蓋請核對）'),pack,batch['captured_at'])
  db.commit()

def latest_quotes(db):
 return [dict(r) for r in db.execute('SELECT p.*,r.captured_at,r.method,r.pack_code FROM dora_quotes p JOIN dora_runs r ON r.id=p.run_id WHERE r.id=(SELECT MAX(x.id) FROM dora_runs x WHERE x.pack_code=r.pack_code)')]

def coverage(db,series=''):
 latest={r['pack_code']:dict(r) for r in db.execute('SELECT * FROM dora_runs r WHERE id=(SELECT MAX(x.id) FROM dora_runs x WHERE x.pack_code=r.pack_code)')}
 return [dict(src,latest=latest.get(src['pack'])) for src in SOURCES if not series or src['series_code']==series]

def update_live(pack='csm1'):
 source=source_info(pack);reserve('dorasuta:'+source['sid']);html=fetch_page(source['url']);items,total=parse_html(html,pack)
 if total is None or len(items)<total:raise UpdateError('目前只能辨識部分商品，未取代該分類舊資料。請匯入需要的分頁內容。')
 with get_connection() as db:save_snapshot(db,items,total,'直接來源 HTML',pack);db.commit()
 return len(items)

@bp.route('/admin/dorasuta',methods=['GET','POST'])
def manage():
 if not session.get('is_admin'):return redirect(url_for('admin_login'))
 from series import pack_arg,series_arg
 pack=pack_arg('csm1');series=series_arg()
 if pack not in BY_PACK:abort(400,'請選擇有效的 Dorasuta 分類。')
 source=source_info(pack)
 if request.method=='POST':
  try:
   if request.form.get('action')=='update':count=update_live(pack)
   else:
    uploads=request.files.getlist('pages');text=request.form.get('page_text','');items={};totals=[]
    if len(uploads)>10:raise UpdateError('一次最多匯入 10 個頁面。')
    batches=[]
    for upload in uploads:
     if not upload.filename:continue
     raw=upload.read(4_000_001)
     if len(raw)>4_000_000:raise UpdateError('單一頁面超過 4 MB。')
     try:batches.append(parse_html(raw.decode('utf-8-sig'),pack))
     except UnicodeError as e:raise UpdateError('請使用 UTF-8 網頁檔。') from e
    if text.strip():batches.append(parse_text(text,pack=pack))
    if not batches:raise UpdateError('請選擇 HTML 檔案或貼上頁面文字。')
    for parsed,total in batches:
     if total:totals.append(total)
     for p in parsed:
      if p['quote_id'] in items and items[p['quote_id']]['price_jpy']!=p['price_jpy']:raise UpdateError('不同頁面的同商品價格衝突，請核對後再匯入。')
      items[p['quote_id']]=p
    with get_connection() as db:save_snapshot(db,list(items.values()),max(totals) if totals else None,'手動匯入（以提交內容為準）',pack);db.commit()
    count=len(items)
   flash(f'已保存 {count} 筆 Dorasuta 報價，價格比較頁已更新。','success')
  except UpdateError as e:flash(str(e),'error')
  except Exception:current_app.logger.exception('Dorasuta import failed');flash('匯入失敗，原有資料已保留。','error')
  return redirect(url_for('dorasuta.manage',pack=pack))
 with get_connection() as db:
  rows=coverage(db,series);latest=db.execute('SELECT * FROM dora_runs WHERE pack_code=? ORDER BY id DESC LIMIT 1',(pack,)).fetchone()
 return render_template('dorasuta_admin.html',latest=latest,source_url=source['url'],selected_pack=pack,selected_series=series,sources=SOURCES,source=source,coverage=rows)
