"""Min/max across collected retail quotes of the same printed card variant."""
from collections import defaultdict
from datetime import datetime,timezone,timedelta
from flask import Blueprint,render_template,request,url_for
from database import get_connection
from series import PACKS,series_arg,pagination
from catalog import overrides_map
from card_image_match import catalog_image_index,choose_card_image
from yuyutei import latest_products
from dorasuta import latest_quotes,identity,edition,coverage,BY_PACK
bp=Blueprint('comparisons',__name__)

def grouped_prices(sales,dora):
 groups={}
 for source,items in [('悠悠亭',sales),('Dorasuta',dora)]:
  for p in items:
   if source=='悠悠亭':
    if p['pack_code'] not in PACKS:continue
    condition='プレイ用' if any(x in p['card_name'] for x in ('プレイ用','傷あり')) else '一般標示'
    variant=edition(p['card_name'],p['card_number']);key=identity(p['card_number'],p['rarity'],condition,variant)
    # An unclassified edition must not be matched to an otherwise identical listing.
    if '傷あり' in p['card_name']:key=key[:2]+('個別卡況',variant)
    quote_id=p['product_id'];series=PACKS[p['pack_code']]['series_code'];promotion='';scope='yuyu'
   else:
    key=identity(p['card_number'],p['rarity'],p['condition'],p['variant']);quote_id=p['quote_id'];series=p['series_code'];promotion=p['promotion'];scope='dorasuta'
   group=groups.setdefault(key,dict(card_number=key[0],rarity=key[1],condition=key[2],variant=key[3],series_code=series,card_name=p['card_name'],quotes=[]))
   group['quotes'].append(dict(store=source,item_id=quote_id,scope=scope,price_jpy=p['price_jpy'],stock=p['stock'],source_url=p['source_url'],captured_at=p['captured_at'],method=p.get('method','直接來源快照'),promotion=promotion,image_url=p.get('image_url',''),pack_code=p.get('pack_code','csm1')))
 return list(groups.values())

@bp.route('/prices/compare')
def prices():
 series=series_arg();keyword=request.args.get('keyword','').strip()[:100];sort=request.args.get('sort','high');sort=sort if sort in ('high','low') else 'high';stock='all';view='all'
 selected_pack=request.args.get('dora_pack','')
 if selected_pack and selected_pack not in BY_PACK:selected_pack=''
 if selected_pack and series and BY_PACK[selected_pack]['series_code']!=series:selected_pack=''
 if stock not in ('all','available'):stock='all'
 if view not in ('all','dorasuta','matched'):view='dorasuta'
 with get_connection() as db:
  groups=grouped_prices(latest_products(db),latest_quotes(db));overrides=overrides_map(db);catalog_images=catalog_image_index(db);coverage_rows=coverage(db,series)
 rows=[]
 for g in groups:
  stores={q['store'] for q in g['quotes']}
  if series and g['series_code']!=series:continue
  if view=='dorasuta' and 'Dorasuta' not in stores or view=='matched' and len(stores)<2:continue
  if keyword.casefold() not in (g['card_name']+' '+g['card_number']+' '+g['rarity']).casefold():continue
  quotes=[q for q in g['quotes'] if (not selected_pack or q['pack_code']==selected_pack) and (stock!='available' or q['stock']=='有貨')]
  selected_stores={q['store'] for q in quotes}
  if view=='dorasuta' and 'Dorasuta' not in selected_stores or view=='matched' and len(selected_stores)<2:continue
  if not quotes:continue
  quotes.sort(key=lambda q:q['price_jpy']);g['quotes']=quotes;g['minimum']=quotes[0]['price_jpy'];g['maximum']=quotes[-1]['price_jpy'];g['store_count']=len({q['store'] for q in quotes});g['spread']=g['maximum']-g['minimum']
  g['minimum_stores']='、'.join(sorted({q['store'] for q in quotes if q['price_jpy']==g['minimum']}));g['maximum_stores']='、'.join(sorted({q['store'] for q in quotes if q['price_jpy']==g['maximum']}))
  available=[q for q in quotes if q['stock']=='有貨']
  g['available_minimum']=min((q['price_jpy'] for q in available),default=None)
  g['available_stores']='、'.join(sorted({q['store'] for q in available if q['price_jpy']==g['available_minimum']}))
  g['display_image']=next((choose_card_image(q['scope'],q['item_id'],g['card_number'],q['image_url'],overrides,catalog_images) for q in quotes if overrides.get((q['scope'],q['item_id'])) or q['image_url']),catalog_images.get(g['card_number'].strip().upper(),''))
  rows.append(g)
 rows.sort(key=lambda g:(g['maximum'],g['card_number']),reverse=(sort=='high'));rows,paging=pagination(rows,20)
 return render_template('comparisons.html',cards=rows,paging=paging,sort=sort,selected_series=series,keyword=keyword,stock=stock,view=view,coverage=coverage_rows,selected_pack=selected_pack,coverage_count=sum(c['latest']['quote_count'] for c in coverage_rows if c['latest']))
