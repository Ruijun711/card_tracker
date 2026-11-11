"""Image/effect encyclopedia and authenticated image overrides."""
import json,re
from pathlib import Path
from urllib.parse import urljoin,urlparse
from uuid import uuid4
from io import BytesIO
from html import escape
from bs4 import BeautifulSoup, NavigableString
from flask import Blueprint,request,session,redirect,url_for,render_template,flash,abort,current_app
from PIL import Image,UnidentifiedImageError
from database import get_connection
from series import SERIES,BY_CODE,PACKS,series_arg,pagination
from source_fetch import UpdateError,fetch_page,reserve
from datetime import datetime,timezone
bp=Blueprint('catalog',__name__)
SOURCE='https://rugiacreation.com/ua/search?Name=HK&Version=CSM'

def safe_image(value, host):
    u=urlparse(value)
    return value if u.scheme=='https' and u.hostname==host and not u.username else ''

def effect_markup(node):
    if not node:return ''
    def convert(n):
        if isinstance(n,NavigableString):return escape(str(n))
        if n.name in ('script','style','iframe'):return ''
        if n.name=='img':
            src=safe_image(urljoin(SOURCE,n.get('src','')),'rugiacreation.com')
            label=n.get('alt') or Path(urlparse(src).path).stem
            return f'<img class="effect-icon" src="{escape(src,quote=True)}" alt="{escape(label,quote=True)}" loading="lazy">' if src else ''
        if n.name=='br':return '<br>'
        content=''.join(convert(c) for c in n.children)
        return '<span class="effect-timing">'+content+'</span>' if 'timing' in n.get('class',[]) else content
    return ''.join(convert(n) for n in node.children).strip()

def parse_catalog(html):
    if "</html>" not in html.lower():raise ValueError("來源頁面不完整，已停止更新")
    soup=BeautifulSoup(html,'html.parser');result=[];seen=set()
    for c in soup.select('.cardHolder[id]'):
        key=c['id'];img=c.select_one('img[data-src]');info=c.select_one('.infoBox')
        if not re.fullmatch(r'[A-Za-z0-9_-]+',key) or not img or not info:continue
        links=info.select('a[href*="Find1="]');name=info.select_one('.fontsize2')
        if len(links)<2:raise ValueError('圖鑑卡號欄位不完整')
        number=links[0].get_text(strip=True)+'/'+links[1].get_text(strip=True)
        image=safe_image(urljoin(SOURCE,img['data-src']),'rugiacreation.com')
        if not image or key in seen:raise ValueError('圖鑑圖片或識別碼異常')
        seen.add(key)
        result.append(dict(card_id=key,card_number=number,card_name=name.get_text(strip=True) if name else number,
            image_url=image,effect_html=effect_markup(c.select_one('.effect')),trigger_html=effect_markup(c.select_one('.trigger'))))
    if not result:raise ValueError('找不到圖鑑資料')
    return result

def init_catalog():
    with get_connection() as db:
        db.executescript("""CREATE TABLE IF NOT EXISTS encyclopedia (
            card_id TEXT PRIMARY KEY,card_number TEXT NOT NULL,card_name TEXT NOT NULL,
            image_url TEXT NOT NULL,effect_html TEXT NOT NULL,trigger_html TEXT NOT NULL,series_code TEXT NOT NULL DEFAULT 'CSM');
            CREATE TABLE IF NOT EXISTS image_overrides(scope TEXT NOT NULL,item_id TEXT NOT NULL,image_path TEXT NOT NULL,PRIMARY KEY(scope,item_id));
            CREATE TABLE IF NOT EXISTS catalog_overrides(card_id TEXT PRIMARY KEY,card_name TEXT NOT NULL DEFAULT '',effect_text TEXT NOT NULL DEFAULT '',trigger_text TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS catalog_sync(series_code TEXT PRIMARY KEY,updated_at TEXT NOT NULL);""")
        if 'series_code' not in {r['name'] for r in db.execute('PRAGMA table_info(encyclopedia)')}:
            db.execute("ALTER TABLE encyclopedia ADD COLUMN series_code TEXT NOT NULL DEFAULT 'CSM'")
        seed=Path(__file__).with_name('catalog_seed.json')
        if seed.exists():
            for c in json.loads(seed.read_text(encoding='utf-8')):
                db.execute('INSERT OR IGNORE INTO encyclopedia(card_id,card_number,card_name,image_url,effect_html,trigger_html,series_code) VALUES(?,?,?,?,?,?,?)',tuple(c[k] for k in ('card_id','card_number','card_name','image_url','effect_html','trigger_html'))+(c.get('series_code','CSM'),))
        db.commit()

def apply_text_overrides(db,cards):
    custom={r['card_id']:r for r in db.execute('SELECT * FROM catalog_overrides')}
    for c in cards:
        o=custom.get(c['card_id'])
        if o:
            if o['card_name']:c['card_name']=o['card_name']
            for field,target in [('effect_text','effect_html'),('trigger_text','trigger_html')]:
                if o[field]:c[target]=escape(o[field]).replace('\n','<br>')
    return cards

def sync_catalog(code):
    if code not in BY_CODE:raise UpdateError('未知系列')
    reserve('catalog:'+code)
    try:cards=parse_catalog(fetch_page('https://rugiacreation.com/ua/search?Name=HK&Version='+code))
    except ValueError as e:raise UpdateError(str(e)) from e
    if any('/'+code+'-' not in c['card_number'] for c in cards):raise UpdateError('來源卡號與選擇系列不符，已停止更新。')
    with get_connection() as db:
        for c in cards:
            db.execute("""INSERT INTO encyclopedia(card_id,card_number,card_name,image_url,effect_html,trigger_html,series_code) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(card_id) DO UPDATE SET card_name=excluded.card_name,image_url=excluded.image_url,effect_html=excluded.effect_html,trigger_html=excluded.trigger_html""",tuple(c[k] for k in ('card_id','card_number','card_name','image_url','effect_html','trigger_html'))+(code,))
        db.execute('INSERT INTO catalog_sync VALUES(?,?) ON CONFLICT(series_code) DO UPDATE SET updated_at=excluded.updated_at',(code,datetime.now(timezone.utc).isoformat(timespec='seconds')));db.commit()
    return len(cards)

def resolved_image(scope,key,remote,overrides):
    local=overrides.get((scope,key))
    return url_for('static',filename=local) if local else remote

def overrides_map(db):
    return {(r['scope'],r['item_id']):r['image_path'] for r in db.execute('SELECT * FROM image_overrides')}

def home_view():
    keyword=request.args.get('keyword','').strip()[:100];series=series_arg()
    with get_connection() as db:
        cards=apply_text_overrides(db,[dict(r) for r in db.execute('SELECT * FROM encyclopedia ORDER BY series_code,card_id')]);overrides=overrides_map(db)
    cards=[c for c in cards if (not series or c['series_code']==series) and keyword.casefold() in (c['card_name']+' '+c['card_number']).casefold()]
    cards,paging=pagination(cards)
    for c in cards:c['display_image']=resolved_image('catalog',c['card_id'],c['image_url'],overrides)
    return render_template('encyclopedia.html',cards=cards,keyword=keyword,selected_series=series,paging=paging)

@bp.route('/admin/catalog/update',methods=['POST'])
def update_catalog():
    if not session.get('is_admin'):return redirect(url_for('admin_login'))
    code=series_arg('CSM')
    try:flash(f"{BY_CODE[code]['name']}：已同步 {sync_catalog(code)} 張圖鑑。",'success')
    except UpdateError as e:flash(str(e),'error')
    except Exception:
        current_app.logger.exception('Catalog sync failed');flash('圖鑑更新失敗，原有資料已保留。','error')
    return redirect(url_for('admin_dashboard',series=code))

@bp.route('/admin/catalog')
def manage_catalog():
    if not session.get('is_admin'):return redirect(url_for('admin_login'))
    series=series_arg();keyword=request.args.get('keyword','').strip()[:100]
    with get_connection() as db:
        rows=apply_text_overrides(db,[dict(r) for r in db.execute('SELECT * FROM encyclopedia ORDER BY series_code,card_id')]);overrides=overrides_map(db)
    rows=[r for r in rows if (not series or r['series_code']==series) and keyword.casefold() in (r['card_name']+' '+r['card_number']).casefold()]
    rows,paging=pagination(rows)
    for r in rows:r['display_image']=resolved_image('catalog',r['card_id'],r['image_url'],overrides)
    return render_template('catalog_manage.html',cards=rows,selected_series=series,keyword=keyword,paging=paging)

@bp.route('/admin/catalog/<card_id>',methods=['GET','POST'])
def edit_catalog(card_id):
    if not session.get('is_admin'):return redirect(url_for('admin_login'))
    with get_connection() as db:
        card=db.execute('SELECT * FROM encyclopedia WHERE card_id=?',(card_id,)).fetchone()
        if not card:abort(404)
        if request.method=='POST':
            values=[request.form.get(k,'').strip() for k in ('card_name','effect_text','trigger_text')]
            if len(values[0])>150 or any(len(v)>5000 for v in values[1:]):abort(400,'文字過長')
            db.execute('INSERT INTO catalog_overrides VALUES(?,?,?,?) ON CONFLICT(card_id) DO UPDATE SET card_name=excluded.card_name,effect_text=excluded.effect_text,trigger_text=excluded.trigger_text',(card_id,*values));db.commit()
            flash('圖鑑內容已儲存；留空欄位會使用來源內容。','success');return redirect(url_for('catalog.edit_catalog',card_id=card_id))
        custom=db.execute('SELECT * FROM catalog_overrides WHERE card_id=?',(card_id,)).fetchone()
    return render_template('catalog_edit.html',card=card,custom=custom)

@bp.route('/admin/card-media',methods=['GET','POST'])
def media():
    if not session.get('is_admin'):return redirect(url_for('admin_login'))
    scope=request.values.get('scope','yuyu')
    if scope not in ('yuyu','catalog','dorasuta'):abort(400)
    series=series_arg();state=request.args.get('state','all')
    with get_connection() as db:
        if scope=='catalog':
            rows=[dict(r) for r in db.execute('SELECT card_id AS item_id,card_name,card_number,image_url,series_code FROM encyclopedia ORDER BY series_code,card_id')]
        elif scope=='dorasuta':
            from dorasuta import latest_quotes
            rows=latest_quotes(db)
            for r in rows:r['item_id']=r['quote_id']
        else:
            from yuyutei import latest_products
            rows=latest_products(db)
            from buyback import latest_offers
            known={r['product_id'] for r in rows}
            rows.extend(r for r in latest_offers(db) if r['product_id'] not in known)
            for r in rows:r['item_id']=r['product_id'];r['series_code']=PACKS.get(r['pack_code'],{}).get('series_code','')
        overrides=overrides_map(db)
    if request.method=='POST':
        item_id=request.form.get('item_id','')
        if item_id not in {r['item_id'] for r in rows}:abort(404)
        upload=request.files.get('image')
        if not upload or not upload.filename:
            flash('請選擇 JPG、PNG 或 WebP 圖片。','error');return redirect(url_for('catalog.media',scope=scope,item=item_id))
        try:
            raw=upload.read(5_000_001)
            if len(raw)>5_000_000:raise ValueError()
            im=Image.open(BytesIO(raw))
            if im.format not in ('JPEG','PNG','WEBP') or im.width*im.height>16_000_000:raise ValueError()
            im.verify();im=Image.open(BytesIO(raw)).convert('RGB');im.thumbnail((1200,1600))
        except (OSError,ValueError,UnidentifiedImageError,Image.DecompressionBombError):
            flash('圖片無法辨識或過大，請使用 5 MB 以內的 JPG、PNG、WebP。','error')
            return redirect(url_for('catalog.media',scope=scope,item=item_id))
        rel='card_images/'+uuid4().hex+'.webp';dest=Path(current_app.static_folder)/rel
        dest.parent.mkdir(parents=True,exist_ok=True);im.save(dest,'WEBP',quality=90)
        try:
            with get_connection() as db:
                db.execute('INSERT INTO image_overrides VALUES(?,?,?) ON CONFLICT(scope,item_id) DO UPDATE SET image_path=excluded.image_path',(scope,item_id,rel));db.commit()
        except Exception:
            dest.unlink(missing_ok=True);raise
        flash('卡圖已儲存；之後更新價格也會保留這張圖片。','success')
        return redirect(url_for('catalog.media',scope=scope,item=item_id))
    keyword=request.args.get('keyword','').strip()[:100];item=request.args.get('item','')
    for r in rows:
        r['display_image']=resolved_image(scope,r['item_id'],r['image_url'],overrides)
        r['custom']=bool(overrides.get((scope,r['item_id'])))
    rows=[r for r in rows if (not series or r['series_code']==series) and (state!='missing' or not r['display_image']) and (state!='custom' or r['custom']) and (not item or r['item_id']==item) and keyword.casefold() in (r['card_name']+' '+r['card_number']+' '+r['item_id']).casefold()]
    rows,paging=pagination(rows)
    return render_template('card_media.html',cards=rows,scope=scope,keyword=keyword,selected_series=series,state=state,paging=paging)
