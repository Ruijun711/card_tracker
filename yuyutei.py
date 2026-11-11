"""Per-pack JPY snapshots; product identity includes the source pack."""
import json,re
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from flask import Blueprint,render_template,request,session,redirect,url_for,flash,abort
from database import get_connection
from bs4 import BeautifulSoup
from series import SERIES,PACKS,BY_CODE,series_arg,pack_arg,pagination
from source_fetch import UpdateError,fetch_page,reserve
bp=Blueprint('yuyutei',__name__)
def fetch_html(pack='csm1'):
    if pack not in PACKS:raise UpdateError('未知的商品分類')
    return fetch_page('https://yuyu-tei.jp/sell/ua/s/'+pack)

def product_image(value):
    from urllib.parse import urlparse
    u=urlparse(value)
    return value if u.scheme=='https' and u.hostname=='card.yuyu-tei.jp' and not u.username and 'noimage' not in u.path.lower() else ''

def parse_products(html, pack="csm1"):
    if "</html>" not in html.lower():raise UpdateError("來源頁面不完整，已取消更新。")
    if pack not in PACKS: raise UpdateError("未知的商品分類")
    soup = BeautifulSoup(html, 'html.parser')
    products = {}
    blocks = soup.select('.card-product')
    if not blocks:
        raise UpdateError('找不到商品資料，可能是網站阻擋或版面改變；未寫入任何價格。')
    for block in blocks:
        link = block.select_one(f'a[href*="/sell/ua/card/{pack}/"]')
        title = block.select_one('h4')
        picture = block.select_one('.product-img img')
        price_nodes = block.select('strong')
        stock = block.select_one('.cart_sell_zaiko')
        number_node = block.select_one('span.border')
        if not all([link, title, picture, stock, number_node]):
            raise UpdateError('商品欄位不完整，已取消整批更新；舊資料已保留。')
        url = link.get('href', '')
        match = re.fullmatch(r'https://yuyu-tei\.jp/sell/ua/card/' + re.escape(pack) + r'/(\d+)', url)
        prices = [re.fullmatch(r'([0-9,]+)\s*円', n.get_text(' ', strip=True)) for n in price_nodes]
        prices = [m for m in prices if m]
        number = number_node.get_text(strip=True)
        alt = picture.get('alt', '')
        meta = re.fullmatch(re.escape(number) + r'\s+(\S+)\s+(.+)', alt)
        if not match or len(prices) != 1 or not meta or not title.get_text(strip=True):
            raise UpdateError('無法可靠辨識商品版本或售價，已取消整批更新。')
        price = int(prices[0].group(1).replace(',', ''))
        if price <= 0 or price > 100_000_000:
            raise UpdateError('偵測到不合理價格，已取消整批更新。')
        stock_text = stock.get_text(' ', strip=True)
        if 'sold-out' in block.get('class', []) or '×' in stock_text:
            status = '缺貨'
        elif re.search(r'在庫\s*:\s*[1-9][0-9]*\s*点', stock_text):
            status = '有貨'
        else:
            status = '未確認'
        item = dict(product_id=pack+":"+match.group(1), card_number=number,
                    card_name=title.get_text(strip=True), rarity=meta.group(1),
                    price_jpy=price, stock=status, source_url=url,
                    image_url=product_image(picture.get('src','')))
        if item['product_id'] in products and products[item['product_id']] != item:
            raise UpdateError('來源出現衝突商品資料，已取消整批更新。')
        products[item['product_id']] = item
    return list(products.values())

def save_snapshot(db,products,pack,captured):
 run_id=db.execute('INSERT INTO yuyu_runs(captured_at,product_count,pack_code) VALUES(?,?,?)',(captured,len(products),pack)).lastrowid
 db.executemany('''INSERT INTO yuyu_prices(run_id,product_id,card_number,card_name,rarity,price_jpy,stock,source_url,image_url) VALUES(?,?,?,?,?,?,?,?,?)''',[(run_id,p['product_id'],p['card_number'],p['card_name'],p['rarity'],p['price_jpy'],p['stock'],p['source_url'],p.get('image_url','')) for p in products])

def init_tables():
 with get_connection() as db:
  db.executescript('''CREATE TABLE IF NOT EXISTS yuyu_runs(id INTEGER PRIMARY KEY,captured_at TEXT NOT NULL,product_count INTEGER NOT NULL,pack_code TEXT NOT NULL DEFAULT 'csm1');
   CREATE TABLE IF NOT EXISTS yuyu_prices(run_id INTEGER NOT NULL,product_id TEXT NOT NULL,card_number TEXT NOT NULL,card_name TEXT NOT NULL,rarity TEXT NOT NULL,price_jpy INTEGER NOT NULL CHECK(price_jpy>0),stock TEXT NOT NULL,source_url TEXT NOT NULL,image_url TEXT NOT NULL DEFAULT '',PRIMARY KEY(run_id,product_id));
   CREATE INDEX IF NOT EXISTS yuyu_history ON yuyu_prices(product_id,run_id);''')
  if 'pack_code' not in {r['name'] for r in db.execute('PRAGMA table_info(yuyu_runs)')}:db.execute("ALTER TABLE yuyu_runs ADD COLUMN pack_code TEXT NOT NULL DEFAULT 'csm1'")
  if 'image_url' not in {r['name'] for r in db.execute('PRAGMA table_info(yuyu_prices)')}:db.execute("ALTER TABLE yuyu_prices ADD COLUMN image_url TEXT NOT NULL DEFAULT ''")
  db.execute('CREATE INDEX IF NOT EXISTS yuyu_runs_pack ON yuyu_runs(pack_code,id)')
  # Older builds used numeric product IDs for CSM1. Migrate once, including images.
  db.execute("UPDATE yuyu_prices SET product_id='csm1:'||product_id WHERE instr(product_id,':')=0")
  if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='image_overrides'").fetchone():
   db.execute("UPDATE image_overrides SET item_id='csm1:'||item_id WHERE scope='yuyu' AND instr(item_id,':')=0")
  seed=Path(__file__).with_name('prices_seed.json')
  if seed.exists():
   for batch in json.loads(seed.read_text(encoding='utf-8')):
    if not db.execute('SELECT 1 FROM yuyu_runs WHERE pack_code=? LIMIT 1',(batch['pack'],)).fetchone():save_snapshot(db,batch['products'],batch['pack'],batch['captured_at'])
  db.commit()

def update_prices(pack='csm1'):
 if pack not in PACKS:raise UpdateError('未知的商品分類')
 reserve('yuyu:'+pack)
 products=parse_products(fetch_html(pack),pack)
 with get_connection() as db:save_snapshot(db,products,pack,datetime.now(timezone.utc).isoformat(timespec='seconds'));db.commit()
 return len(products)

@bp.app_template_filter('taipei_time')
def taipei_time(value):
 return datetime.fromisoformat(value).astimezone(ZoneInfo('Asia/Taipei')).strftime('%Y/%m/%d %H:%M') if value else '尚未更新'

def latest_products(db):
 return [dict(r) for r in db.execute('''SELECT p.*,r.pack_code,r.captured_at,
  (SELECT old.price_jpy FROM yuyu_prices old WHERE old.product_id=p.product_id AND old.run_id<p.run_id ORDER BY old.run_id DESC LIMIT 1) AS previous_price
  FROM yuyu_prices p JOIN yuyu_runs r ON r.id=p.run_id
  WHERE r.id=(SELECT MAX(x.id) FROM yuyu_runs x WHERE x.pack_code=r.pack_code)
  ORDER BY p.price_jpy DESC,p.product_id''')]

@bp.route('/yuyutei')
def prices():
 series=series_arg();pack=pack_arg();keyword=request.args.get('keyword','').strip()[:100];sort=request.args.get('sort','high');sort=sort if sort in ('high','low') else 'high';selected=request.args.get('product','')[:60]
 if pack and series and PACKS[pack]['series_code']!=series:pack=''
 from catalog import overrides_map
 from card_image_match import catalog_image_index,choose_card_image
 with get_connection() as db:
  rows=latest_products(db);overrides=overrides_map(db);catalog_images=catalog_image_index(db)
  history=db.execute('SELECT p.*,r.captured_at FROM yuyu_prices p JOIN yuyu_runs r ON r.id=p.run_id WHERE p.product_id=? ORDER BY p.run_id DESC LIMIT 30',(selected,)).fetchall() if selected else []
 cards=[]
 for c in rows:
  meta=PACKS.get(c['pack_code'])
  if not meta:continue
  c['series_code']=meta['series_code'];c['pack_name']=meta['name']
  if series and c['series_code']!=series or pack and c['pack_code']!=pack:continue
  if keyword.casefold() not in (c['card_name']+' '+c['card_number']+' '+c['rarity']).casefold():continue
  c['display_image']=choose_card_image('yuyu',c['product_id'],c['card_number'],c['image_url'],overrides,catalog_images);cards.append(c)
 cards.sort(key=lambda c:(c['price_jpy'],c['product_id']),reverse=(sort=='high'))
 cards,paging=pagination(cards,30)
 return render_template('yuyutei.html',cards=cards,keyword=keyword,selected_series=series,selected_pack=pack,sort=sort,history=history,selected=selected,paging=paging)

@bp.route('/admin/yuyutei/update',methods=['POST'])
def update():
 if not session.get('is_admin'):return redirect(url_for('admin_login'))
 pack=pack_arg('csm1')
 if not pack:abort(400,'請選擇商品分類')
 try:flash(f"{PACKS[pack]['name']}：已更新 {update_prices(pack)} 件商品。",'success')
 except UpdateError as e:flash(str(e),'error')
 except Exception:
  from flask import current_app
  current_app.logger.exception('Yuyu update failed');flash('更新失敗，舊資料已保留。','error')
 return redirect(url_for('admin_dashboard',series=PACKS[pack]['series_code']))
