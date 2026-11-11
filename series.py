"""Explicit source registry: only verified UA series/pack URLs are permitted."""
import json,math
from pathlib import Path
from flask import request,abort
SERIES=json.loads(Path(__file__).with_name('series_registry.json').read_text(encoding='utf-8'))
BY_CODE={s['code']:s for s in SERIES}
PACKS={p['code']:{**p,'series_code':s['code'],'series_name':s['name']} for s in SERIES for p in s['packs']}
def series_arg(default=''):
 code=request.values.get('series',default)
 if code and code not in BY_CODE:abort(400,'未知的作品分類')
 return code
def pack_arg(default=''):
 code=request.values.get('pack',default)
 if code and code not in PACKS:abort(400,'未知的商品分類')
 return code
def pagination(rows,size=24):
 try:page=max(1,int(request.args.get('page','1')))
 except ValueError:page=1
 pages=max(1,math.ceil(len(rows)/size));page=min(page,pages)
 return rows[(page-1)*size:page*size],dict(page=page,pages=pages,total=len(rows))
def register_context(app):
 @app.context_processor
 def series_context():
  def page_url(page):
   from flask import url_for
   args=request.args.to_dict();args['page']=page
   return url_for(request.endpoint,**(request.view_args or {}),**args)
  return dict(series_options=SERIES,series_by_code=BY_CODE,pack_options=PACKS,page_url=page_url)
