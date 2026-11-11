"""Transparent screening of a single shop's quotes, never a return forecast."""
import statistics,unicodedata,re
from datetime import datetime,timezone,timedelta
from zoneinfo import ZoneInfo
from flask import Blueprint,render_template,request
from database import get_connection
from series import PACKS,series_arg,pagination
from yuyutei import latest_products
from buyback import latest_offers
from catalog import overrides_map,resolved_image
bp=Blueprint('recommendations',__name__)

def normalize(text):return re.sub(r'\s+','',unicodedata.normalize('NFKC',text))
def parsed_time(text):
 value=datetime.fromisoformat(text)
 return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
def daily_history(rows,now):
 days={}
 for row in rows:
  ts=parsed_time(row['captured_at'])
  if timedelta(0)<=now-ts<=timedelta(days=90) and row['price_jpy']>0 and row.get('active',1):
   day=ts.astimezone(ZoneInfo('Asia/Taipei')).date()
   if day not in days or ts>days[day][0]:days[day]=(ts,row['price_jpy'])
 return [(day,days[day][1]) for day in sorted(days)]
def history_summary(rows,now):
 daily=daily_history(rows,now);values=[p for d,p in daily]
 span=(daily[-1][0]-daily[0][0]).days if daily else 0
 cv=statistics.pstdev(values)/statistics.mean(values) if values else 0
 peak=0;drawdown=0
 for p in values:
  peak=max(peak,p);drawdown=max(drawdown,(peak-p)/peak)
 return dict(days=len(daily),span=span,cv=cv,drawdown=drawdown,growth=values[-1]/values[0]-1 if len(values)>1 else None)

def select_cards(sales,offers,sale_history,buy_history,now=None):
 now=now or datetime.now(timezone.utc);buys={p['product_id']:p for p in offers};stable=[];growth=[];matched=0
 for sale in sales:
  bid=buys.get(sale['product_id'])
  if not bid or not bid['active'] or sale['price_jpy']<=0 or bid['price_jpy']<=0:continue
  if any(normalize(sale[k])!=normalize(bid[k]) for k in ('card_number','rarity','card_name')):continue
  matched+=1
  st=parsed_time(sale['captured_at']);bt=parsed_time(bid['captured_at'])
  if not (timedelta(0)<=now-st<=timedelta(days=7) and timedelta(0)<=now-bt<=timedelta(days=7)) or abs(st-bt)>timedelta(days=2):continue
  ratio=bid['price_jpy']/sale['price_jpy']
  if sale['stock']!='有貨' or sale['price_jpy']<1000 or bid['price_jpy']<500 or not .6<=ratio<1:continue
  sell_stats=history_summary(sale_history.get(sale['product_id'],[]),now);buy_stats=history_summary(buy_history.get(sale['product_id'],[]),now)
  adequate=sell_stats['days']>=3 and sell_stats['span']>=14
  if adequate and (sell_stats['cv']>.2 or sell_stats['drawdown']>.2):continue
  entry=dict(sale);entry['image_url']=sale['image_url'] or bid['image_url'];entry.update(buy_price=bid['price_jpy'],buy_url=bid['source_url'],buy_time=bid['captured_at'],ratio=ratio,sell_stats=sell_stats,buy_stats=buy_stats,
    confidence='有歷史支持' if adequate else '初步觀察',reasons=[f'店家收購價為售價的 {ratio:.0%}',f'目前有貨且店家列為收購中'])
  if adequate:entry['reasons'].append(f"{sell_stats['days']} 個觀察日、跨 {sell_stats['span']} 天，售價最大回落 {sell_stats['drawdown']:.0%}")
  else:entry['reasons'].append('長期歷史不足，僅依目前收購比率篩選')
  stable.append(entry)
  enough=all(x['days']>=3 and x['span']>=7 for x in [sell_stats,buy_stats])
  if enough and all(x['growth'] is not None and .05<=x['growth']<=.5 for x in [sell_stats,buy_stats]):
   rising=dict(entry);rising['confidence']='雙向價格動能';rising['reasons']=[f"售價在 {sell_stats['span']} 天觀察期間上升 {sell_stats['growth']:.1%}",f"收購價在 {buy_stats['span']} 天觀察期間上升 {buy_stats['growth']:.1%}",f'目前收購／售價比 {ratio:.0%}，反映單一店家的報價變化'];growth.append(rising)
 stable.sort(key=lambda p:(p['confidence']=='有歷史支持',p['ratio'],-p['sell_stats']['cv'],p['product_id']),reverse=True)
 growth.sort(key=lambda p:(min(p['sell_stats']['growth'],p['buy_stats']['growth']),p['ratio']),reverse=True)
 return stable,growth,matched

def recommendation_data(series='',now=None):
 with get_connection() as db:
  sales=[p for p in latest_products(db) if p['pack_code'] in PACKS and (not series or PACKS[p['pack_code']]['series_code']==series)]
  offers=latest_offers(db);overrides=overrides_map(db);sale_history={};buy_history={}
  for table,runs,target in [('yuyu_prices','yuyu_runs',sale_history),('buy_prices','buy_runs',buy_history)]:
   for r in db.execute(f'SELECT p.*,r.captured_at FROM {table} p JOIN {runs} r ON r.id=p.run_id ORDER BY r.id'):
    d=dict(r);target.setdefault(d['product_id'],[]).append(d)
 stable,growth,matched=select_cards(sales,offers,sale_history,buy_history,now)
 for card in stable+growth:
  card['series_code']=PACKS[card['pack_code']]['series_code'];card['display_image']=resolved_image('yuyu',card['product_id'],card['image_url'],overrides)
 return stable,growth,matched

def combined_entries(stable,growth,collectible,mode):
 normal={}
 for name,items in [('保值候選',stable),('增值動能',growth)]:
  for c in items:
   entry=normal.setdefault(c['product_id'],dict(kind='standard',card=dict(c),categories=[]))
   entry['categories'].append(name)
   if name=='增值動能':entry['card']=dict(c)
 if mode in ('stable','growth'):
  chosen=stable if mode=='stable' else growth
  return [dict(kind='standard',card=dict(c),categories=normal[c['product_id']]['categories']) for c in chosen]
 if mode=='collectors':return [dict(kind='collector',card=c,categories=['親筆簽／稀有收藏卡']) for c in collectible]
 entries=[]
 for c in collectible:
  categories=['親筆簽／稀有收藏卡']
  for key in c['yuyu_ids']:
   item=normal.pop(key,None)
   if item:
    for label in item['categories']:
     if label not in categories:categories.append(label)
  entries.append(dict(kind='collector',card=c,categories=categories))
 entries.extend(normal.values())
 return entries

@bp.route('/recommendations')
def cards():
 from collectors import collection_candidates
 series=series_arg();mode=request.args.get('mode','all')
 if mode not in ('all','stable','growth','collectors'):mode='all'
 stable,growth,matched=recommendation_data(series);collectible=collection_candidates(series)
 entries=combined_entries(stable,growth,collectible,mode);entries,paging=pagination(entries,24)
 return render_template('recommendations.html',entries=entries,counts=dict(stable=len(stable),growth=len(growth),collectors=len(collectible)),matched=matched,selected_series=series,mode=mode,paging=paging)
