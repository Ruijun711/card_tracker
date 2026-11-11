"""Source-published JPY buy offers, independent from retail-sale snapshots."""
import json,re
from pathlib import Path
from datetime import datetime,timezone
from bs4 import BeautifulSoup
from flask import Blueprint,render_template,request,session,redirect,url_for,flash,abort,current_app
from database import get_connection
from series import PACKS,series_arg,pack_arg,pagination
from source_fetch import UpdateError,fetch_page,reserve
from yuyutei import product_image
bp=Blueprint('buyback',__name__)

def parse_buyback(html,pack):
 if pack not in PACKS or '</html>' not in html.lower():raise UpdateError('收購來源分類或網頁不完整。')
 soup=BeautifulSoup(html,'html.parser');blocks=soup.select('.card-product');products={}
 if not blocks:raise UpdateError('找不到收購商品，已取消本次更新。')
 for block in blocks:
  link=block.select_one(f'a[href*="/buy/ua/card/{pack}/"]');title=block.select_one('h4');picture=block.select_one('.product-img img');number=block.select_one('span.border')
  if not all([link,title,picture,number]):raise UpdateError('收購商品重要欄位不完整，整批未寫入。')
  url=link.get('href','');match=re.fullmatch(r'https://yuyu-tei\.jp/buy/ua/card/'+re.escape(pack)+r'/(\d+)',url)
  card_number=number.get_text(strip=True);meta=re.fullmatch(re.escape(card_number)+r'\s+(\S+)\s+(.+)',picture.get('alt',''))
  prices=[re.fullmatch(r'([0-9,]+)\s*円',n.get_text(' ',strip=True)) for n in block.select('strong')];prices=[m for m in prices if m]
  if not match or not meta or len(prices)!=1 or not title.get_text(strip=True):raise UpdateError('無法可靠辨識收購商品版本或價格，整批取消。')
  price=int(prices[0].group(1).replace(',',''))
  if price<0 or price>100_000_000:raise UpdateError('收購價異常，整批取消。')
  button=block.select_one('a.cart_sell_in');classes=block.get('class',[])
  active=int(price>0 and button is not None and 'disabled' not in button.get('class',[]) and 'sold-out' not in classes)
  item=dict(product_id=pack+':'+match.group(1),card_number=card_number,card_name=title.get_text(strip=True),rarity=meta.group(1),price_jpy=price,active=active,source_url=url,image_url=product_image(picture.get('src','')),source_up=int('priceup' in classes))
  if item['product_id'] in products and products[item['product_id']]!=item:raise UpdateError('同一收購商品資料衝突，整批取消。')
  products[item['product_id']]=item
 return list(products.values())

def save_snapshot(db,products,pack,captured):
 run=db.execute('INSERT INTO buy_runs(captured_at,product_count,pack_code) VALUES(?,?,?)',(captured,len(products),pack)).lastrowid
 db.executemany('INSERT INTO buy_prices VALUES(?,?,?,?,?,?,?,?,?,?)',[(run,p['product_id'],p['card_number'],p['card_name'],p['rarity'],p['price_jpy'],p['active'],p['source_url'],p['image_url'],p['source_up']) for p in products])

def init_buyback():
 with get_connection() as db:
  db.executescript('''CREATE TABLE IF NOT EXISTS buy_runs(id INTEGER PRIMARY KEY,captured_at TEXT NOT NULL,product_count INTEGER NOT NULL,pack_code TEXT NOT NULL);
   CREATE TABLE IF NOT EXISTS buy_prices(run_id INTEGER NOT NULL,product_id TEXT NOT NULL,card_number TEXT NOT NULL,card_name TEXT NOT NULL,rarity TEXT NOT NULL,price_jpy INTEGER NOT NULL CHECK(price_jpy>=0),active INTEGER NOT NULL,source_url TEXT NOT NULL,image_url TEXT NOT NULL,source_up INTEGER NOT NULL,PRIMARY KEY(run_id,product_id));
   CREATE INDEX IF NOT EXISTS buy_history ON buy_prices(product_id,run_id);
   CREATE INDEX IF NOT EXISTS buy_pack ON buy_runs(pack_code,id);''')
  seed=Path(__file__).with_name('buy_seed.json')
  if seed.exists():
   for batch in json.loads(seed.read_text(encoding='utf-8')):
    if not db.execute('SELECT 1 FROM buy_runs WHERE pack_code=?',(batch['pack'],)).fetchone():save_snapshot(db,batch['products'],batch['pack'],batch['captured_at'])
  db.commit()

def latest_offers(db):
 return [dict(r) for r in db.execute('''SELECT p.*,r.pack_code,r.captured_at,
  (SELECT old.price_jpy FROM buy_prices old WHERE old.product_id=p.product_id AND old.run_id<p.run_id ORDER BY old.run_id DESC LIMIT 1) AS previous_price
  FROM buy_prices p JOIN buy_runs r ON r.id=p.run_id
  WHERE r.id=(SELECT MAX(x.id) FROM buy_runs x WHERE x.pack_code=r.pack_code) ORDER BY p.price_jpy DESC,p.product_id''')]

def update_offers(pack):
 if pack not in PACKS:raise UpdateError('未知收購分類')
 reserve('buy:'+pack);products=parse_buyback(fetch_page('https://yuyu-tei.jp/buy/ua/s/'+pack),pack)
 with get_connection() as db:save_snapshot(db,products,pack,datetime.now(timezone.utc).isoformat(timespec='seconds'));db.commit()
 return len(products)

@bp.route('/buyback')
def offers():
 series=series_arg();pack=pack_arg();keyword=request.args.get('keyword','').strip()[:100];sort=request.args.get('sort','high');sort=sort if sort in ('high','low') else 'high';selected=request.args.get('product','')[:60]
 if pack and series and PACKS[pack]['series_code']!=series:pack=''
 from catalog import overrides_map
 from card_image_match import catalog_image_index,choose_card_image
 from yuyutei import latest_products
 with get_connection() as db:
  rows=latest_offers(db);overrides=overrides_map(db);catalog_images=catalog_image_index(db);sale_images={p['product_id']:p['image_url'] for p in latest_products(db)}
  history=db.execute('SELECT p.*,r.captured_at FROM buy_prices p JOIN buy_runs r ON r.id=p.run_id WHERE product_id=? ORDER BY run_id DESC LIMIT 30',(selected,)).fetchall() if selected else []
 cards=[]
 for c in rows:
  meta=PACKS.get(c['pack_code'])
  if not meta or not c['active'] or c['price_jpy']<=0:continue
  if series and meta['series_code']!=series or pack and c['pack_code']!=pack:continue
  if keyword.casefold() not in (c['card_name']+' '+c['card_number']+' '+c['rarity']).casefold():continue
  c['series_code']=meta['series_code'];c['pack_name']=meta['name'];c['display_image']=choose_card_image('yuyu',c['product_id'],c['card_number'],c['image_url'] or sale_images.get(c['product_id'],''),overrides,catalog_images);cards.append(c)
 cards.sort(key=lambda c:(c['price_jpy'],c['product_id']),reverse=(sort=='high'))
 cards,paging=pagination(cards,30)
 return render_template('buyback.html',cards=cards,selected_series=series,selected_pack=pack,sort=sort,keyword=keyword,selected=selected,history=history,paging=paging)

@bp.route('/admin/buyback/update',methods=['POST'])
def update():
 if not session.get('is_admin'):return redirect(url_for('admin_login'))
 pack=pack_arg()
 if not pack:abort(400,'請選擇收購分類')
 try:flash(f"{PACKS[pack]['name']}：已更新 {update_offers(pack)} 件收購商品。",'success')
 except UpdateError as e:flash(str(e),'error')
 except Exception:current_app.logger.exception('Buyback update failed');flash('收購更新失敗，舊資料已保留。','error')
 return redirect(url_for('admin_dashboard',series=PACKS[pack]['series_code']))
