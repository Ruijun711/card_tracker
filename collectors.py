"""Collector watchlist based on version evidence and research completeness.
No authenticity decision, population estimate, or price forecast is inferred.
"""
import hashlib,json,re,unicodedata
from datetime import datetime,timezone,date
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
from flask import Blueprint,request,session,redirect,url_for,render_template,flash,abort
from database import get_connection
from series import series_arg,pagination,PACKS
from comparisons import grouped_prices
from catalog import overrides_map,resolved_image
from yuyutei import latest_products
from dorasuta import latest_quotes
from buyback import latest_offers
bp=Blueprint('collectors',__name__)
OFFICIAL_CSM='https://www.unionarena-tcg.com/jp/products/boosters/csm-1.php?slide=modal'
TYPE_LABELS={'official_ap':'限定序號 AP／官方親筆企劃','handwritten':'親筆標示（個卡待核對）','printed':'箔押／印刷簽（非親筆）','serial':'AP／序號版（親筆性質待查）','signature_unknown':'簽名標示（類型待查）','rare':'特殊稀有版本','none':'一般版本'}

def init_collectors():
 with get_connection() as db:
  db.execute('CREATE TABLE IF NOT EXISTS collector_metadata(collector_id TEXT PRIMARY KEY,data_json TEXT NOT NULL,updated_at TEXT NOT NULL)');db.commit()
def collector_key(group):
 return hashlib.sha256('|'.join(group[k] for k in ('card_number','rarity','condition','variant')).encode()).hexdigest()[:24]
def automatic_type(group):
 name=unicodedata.normalize('NFKC',group['card_name']);number=group['card_number'];rarity=group['rarity']
 if re.fullmatch(r'UA53BT/CSM-1-AP0[123]★',number) and group['variant']=='AP／序號標示':return 'official_ap'
 if any(word in name for word in ('箔押','印刷','プリント','複製','直筆風')):return 'printed'
 if any(word in name for word in ('直筆ではない','直筆ではありません')):return 'signature_unknown'
 if '直筆' in name or '親筆' in name:return 'handwritten'
 if group['variant']=='AP／序號標示' or 'シリアル' in name:return 'serial'
 if 'サイン' in name or '簽名' in name:return 'signature_unknown'
 if rarity.count('★')>=2 or rarity=='UR' or group['variant']=='WINNER':return 'rare'
 return 'none'
def checked_url(value):
 value=value.strip()
 if not value:return ''
 u=urlparse(value)
 if u.scheme!='https' or not u.hostname or u.username or u.password:raise ValueError('佐證連結請使用完整 HTTPS 網址。')
 return value

def validate_metadata(form):
 data={key:form.get(key,'').strip() for key in ['classification','visibility','evidence_url','evidence_note','auth_note','condition_note','edition_size','edition_url','sale_price','sale_date','sale_url','sale_note']}
 if data['classification'] not in ('auto','handwritten','printed','serial','rare','none'):raise ValueError('未知的收藏類型。')
 if data['visibility'] not in ('auto','include','exclude'):raise ValueError('未知的顯示設定。')
 for key in ('evidence_url','edition_url','sale_url'):data[key]=checked_url(data[key])
 for key in ('evidence_note','auth_note','condition_note','sale_note'):
  if len(data[key])>2000:raise ValueError('說明欄位最多 2,000 字。')
 if data['classification']=='handwritten' and not (data['evidence_url'] and data['evidence_note']):raise ValueError('人工分類為親筆時，請附上來源連結及版本佐證說明；這不等同個卡鑑定。')
 for key in ('edition_size','sale_price'):
  if data[key]:
   if not re.fullmatch(r'[1-9]\d{0,8}',data[key]):raise ValueError('發行量與成交金額須為正整數。')
   data[key]=int(data[key])
  else:data[key]=None
 if data['edition_size'] and not data['edition_url']:raise ValueError('登錄發行量時需附可核對的發行量來源，不能以鑑定送件數代替。')
 if data['sale_price']:
  if not data['sale_url'] or not data['sale_date'] or not data['sale_note']:raise ValueError('成交參考需附日期、來源及同版本／卡況說明。')
  try:day=date.fromisoformat(data['sale_date'])
  except ValueError as e:raise ValueError('成交日期格式不正確。') from e
  if day>datetime.now(ZoneInfo('Asia/Taipei')).date():raise ValueError('成交日期不能是未來日期。')
 return data

def collection_candidates(series='',include_excluded=False):
 with get_connection() as db:
  groups=grouped_prices(latest_products(db),latest_quotes(db));overrides=overrides_map(db)
  metadata={r['collector_id']:json.loads(r['data_json']) for r in db.execute('SELECT * FROM collector_metadata')}
  buys={p['product_id']:p for p in latest_offers(db)}
 result=[]
 for g in groups:
  if series and g['series_code']!=series:continue
  key=collector_key(g);meta=metadata.get(key,{})
  auto=automatic_type(g);kind=meta.get('classification','auto');kind=auto if kind=='auto' else kind
  if kind=='none' and meta.get('visibility')!='include' and not (include_excluded and meta):continue
  excluded=meta.get('visibility')=='exclude' or kind=='none'
  if excluded and not include_excluded:continue
  g=dict(g);g.update(collector_id=key,metadata=meta,kind=kind,auto_kind=auto,type_label=TYPE_LABELS[kind],excluded=excluded,confidence='收藏觀察・個卡待核對',categories=['親筆簽／稀有收藏卡'])
  quotes=sorted(g['quotes'],key=lambda q:q['price_jpy']);g['quotes']=quotes;g['minimum']=quotes[0]['price_jpy'];g['maximum']=quotes[-1]['price_jpy'];g['yuyu_ids']=[q['item_id'] for q in quotes if q['scope']=='yuyu']
  g['display_image']=next((resolved_image(q['scope'],q['item_id'],q['image_url'],overrides) for q in quotes if overrides.get((q['scope'],q['item_id'])) or q['image_url']),'')
  g['buy_reference']=max((buys[q['item_id']]['price_jpy'] for q in quotes if q['scope']=='yuyu' and q['item_id'] in buys and buys[q['item_id']]['active']),default=None)
  # Research fields are explicitly marked as user-entered, not automatically verified.
  g['version_evidence']=meta.get('evidence_url') or (OFFICIAL_CSM if auto=='official_ap' else quotes[0]['source_url'])
  g['edition_size']=meta.get('edition_size');g['reasons']=[];g['missing']=[]
  if kind=='official_ap':
   g['reasons']+=['來源列為限定序號 AP，與一般 AP 分開。','官方商品頁有出演聲優親筆簽企劃說明；單張實物與序號仍需核對。']
   g['missing'].append('官方頁未提供可套用到此卡的確切發行總量；再販封入未定。')
  elif kind=='handwritten':g['reasons'].append('來源或後台標為親筆；需核對實物、簽名來源及可查詢佐證。')
  elif kind=='printed':g['reasons'].append('來源標示箔押／印刷簽，屬收藏版本，不當作親筆簽名。')
  elif kind=='serial':g['reasons'].append('序號／限定 AP 特殊版本；尚無足夠資料判定是否親筆。')
  elif kind=='signature_unknown':g['reasons'].append('來源有簽名標示，但無法區分親筆與印刷，列為待查候選。')
  else:g['reasons'].append(f"來源稀有度／版本標示：{g['rarity']}・{g['variant']}，不以高標價本身判定稀有。")
  if not meta.get('evidence_note'):g['missing'].append('缺少此版本的獨立來源與版本佐證紀錄。')
  if not meta.get('auth_note'):g['missing'].append('未登錄單卡真偽／證書與流轉來源核對說明。')
  if not meta.get('condition_note'):g['missing'].append('缺少單卡卡況、瑕疵與保存說明。')
  if not meta.get('edition_size'):g['missing'].append('發行量未知，不以價格或 PSA 送件數推算。')
  if not meta.get('sale_price'):g['missing'].append('缺少同版本、相近卡況的可查詢實際成交參考。')
  if all(q['stock']!='有貨' for q in quotes):g['missing'].append('僅有缺貨／未確認庫存的店家標價，不代表可買到或可成交。')
  g['fields_complete']=1+int(bool(meta.get('evidence_url') and meta.get('evidence_note')))+int(bool(meta.get('edition_size') and meta.get('edition_url')))+int(bool(meta.get('condition_note')))+int(bool(meta.get('sale_price') and meta.get('sale_url')))
  g['reasons'].append('依版本來源與收藏資料完整度列入觀察，不推估增值幅度或成交機率。')
  result.append(g)
 priority={'official_ap':5,'handwritten':4,'serial':3,'rare':2,'printed':1,'signature_unknown':0,'none':0}
 result.sort(key=lambda g:(-g['fields_complete'],-priority[g['kind']],g['series_code'],g['card_number'],g['rarity']))
 return result

@bp.route('/admin/collectors')
def manage():
 if not session.get('is_admin'):return redirect(url_for('admin_login'))
 series=series_arg();keyword=request.args.get('keyword','').strip()[:100]
 rows=[c for c in collection_candidates(series,True) if keyword.casefold() in (c['card_name']+' '+c['card_number']).casefold()]
 rows,paging=pagination(rows)
 return render_template('collectors_manage.html',cards=rows,paging=paging,selected_series=series,keyword=keyword)

@bp.route('/admin/collectors/<collector_id>',methods=['GET','POST'])
def edit(collector_id):
 if not session.get('is_admin'):return redirect(url_for('admin_login'))
 card=next((c for c in collection_candidates(include_excluded=True) if c['collector_id']==collector_id),None)
 if not card:abort(404)
 if request.method=='POST':
  try:
   data=validate_metadata(request.form)
   with get_connection() as db:db.execute('INSERT INTO collector_metadata VALUES(?,?,?) ON CONFLICT(collector_id) DO UPDATE SET data_json=excluded.data_json,updated_at=excluded.updated_at',(collector_id,json.dumps(data,ensure_ascii=False),datetime.now(timezone.utc).isoformat(timespec='seconds')));db.commit()
   flash('收藏研究資料已儲存；人工登錄內容會標明來源，並非自動鑑定結果。','success')
  except ValueError as e:flash(str(e),'error')
  return redirect(url_for('collectors.edit',collector_id=collector_id))
 return render_template('collector_edit.html',card=card,meta=card['metadata'],type_labels=TYPE_LABELS)
