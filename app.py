import os
from secrets import token_urlsafe
from hmac import compare_digest
from flask import Flask,render_template,request,redirect,url_for,session,abort,flash
from werkzeug.security import generate_password_hash,check_password_hash
from database import get_connection
from series import SERIES,PACKS,series_arg,register_context
from source_fetch import init_control
from yuyutei import bp as yuyu_bp,init_tables,latest_products
from catalog import bp as catalog_bp,init_catalog,home_view
from buyback import bp as buy_bp,init_buyback
from recommendations import bp as recommendations_bp
from dorasuta import bp as dora_bp,init_dorasuta
from comparisons import bp as comparisons_bp
from collectors import bp as collectors_bp,init_collectors
app=Flask(__name__)
app.secret_key=os.environ.get('CARD_TRACKER_SECRET_KEY')
password=os.environ.get('CARD_TRACKER_ADMIN_PASSWORD')
if not app.secret_key or not password:raise RuntimeError('請設定 CARD_TRACKER_SECRET_KEY 和 CARD_TRACKER_ADMIN_PASSWORD')
password_hash=generate_password_hash(password)
app.config.update(SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',MAX_CONTENT_LENGTH=5*1024*1024)
init_control();init_tables();init_catalog();init_buyback();init_dorasuta();init_collectors()
app.register_blueprint(yuyu_bp);app.register_blueprint(catalog_bp);app.register_blueprint(buy_bp);app.register_blueprint(recommendations_bp);app.register_blueprint(dora_bp);app.register_blueprint(comparisons_bp);app.register_blueprint(collectors_bp);register_context(app)
@app.context_processor
def csrf():
 if 'csrf_token' not in session:session['csrf_token']=token_urlsafe(32)
 return {'csrf_token':session['csrf_token']}
@app.before_request
def check_csrf():
 if request.method=='POST' and request.url_rule is not None:
  expected=session.get('csrf_token','');supplied=request.form.get('csrf_token','')
  if not expected or not compare_digest(expected,supplied):abort(400,'表單驗證失敗，請重新整理後再試。')
@app.errorhandler(413)
def large_upload(error):return '上傳內容過大，請將整次提交控制在 5 MB 以內。',413
@app.route('/')
def home():return home_view()
@app.route('/admin/login',methods=['GET','POST'])
def admin_login():
 if request.method=='POST':
  if check_password_hash(password_hash,request.form.get('password','')):
   session.clear();session['is_admin']=True;session['csrf_token']=token_urlsafe(32);return redirect(url_for('admin_dashboard'))
  flash('密碼不正確','error')
 return render_template('admin_login.html')
@app.route('/admin/logout',methods=['POST'])
def admin_logout():
 session.clear();return redirect(url_for('home'))
@app.route('/admin')
def admin_dashboard():
 if not session.get('is_admin'):return redirect(url_for('admin_login'))
 selected=series_arg()
 with get_connection() as db:
  counts={r[0]:r[1] for r in db.execute('SELECT series_code,COUNT(*) FROM encyclopedia GROUP BY series_code')}
  syncs={r[0]:r[1] for r in db.execute('SELECT * FROM catalog_sync')}
  latest={r['pack_code']:dict(r) for r in db.execute('SELECT * FROM yuyu_runs r WHERE id=(SELECT MAX(id) FROM yuyu_runs WHERE pack_code=r.pack_code)')}
  buy_latest={r['pack_code']:dict(r) for r in db.execute('SELECT * FROM buy_runs r WHERE id=(SELECT MAX(id) FROM buy_runs WHERE pack_code=r.pack_code)')}
  products=latest_products(db);overrides={(r[0],r[1]) for r in db.execute('SELECT scope,item_id FROM image_overrides')}
 rows=[dict(s,catalog_count=counts.get(s['code'],0),synced=syncs.get(s['code'])) for s in SERIES if not selected or s['code']==selected]
 stats={'series':len(SERIES),'cards':sum(counts.values()),'products':len(products),'missing':sum(not p['image_url'] and ('yuyu',p['product_id']) not in overrides for p in products)}
 return render_template('admin_dashboard.html',rows=rows,stats=stats,latest=latest,buy_latest=buy_latest,selected_series=selected)
if __name__=='__main__':app.run(debug=os.environ.get('FLASK_DEBUG')=='1')
