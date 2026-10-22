import os,re,json,sqlite3,secrets,subprocess,time,shutil
from pathlib import Path
from functools import wraps
from flask import Flask,render_template,request,redirect,url_for,session,flash,jsonify,abort
from werkzeug.utils import secure_filename
BASE=Path(os.getenv('DATA_DIR','/data')); UP=BASE/'uploads'; UP.mkdir(parents=True,exist_ok=True); DB=BASE/'bots.sqlite3'
SECRET=os.getenv('APP_SECRET'); PASSWORD=os.getenv('ADMIN_PASSWORD')
if not SECRET or not PASSWORD or len(PASSWORD)<14: raise RuntimeError('Set APP_SECRET and ADMIN_PASSWORD (14+ chars).')
app=Flask(__name__); app.secret_key=SECRET; app.config.update(MAX_CONTENT_LENGTH=5*1024*1024,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',SESSION_COOKIE_SECURE=os.getenv('COOKIE_SECURE','0')=='1')
def db():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c
with db() as c: c.execute('CREATE TABLE IF NOT EXISTS bots(id TEXT PRIMARY KEY,name TEXT,filename TEXT,container_id TEXT,status TEXT,created REAL,env TEXT,requirements TEXT)')
def docker(*a): return subprocess.run(['docker',*a],capture_output=True,text=True,timeout=120)
def auth(f):
 @wraps(f)
 def w(*a,**k):
  if not session.get('auth'): return redirect(url_for('login'))
  return f(*a,**k)
 return w
def bot(i):
 with db() as c: r=c.execute('SELECT * FROM bots WHERE id=?',(i,)).fetchone()
 if not r: abort(404)
 return r
@app.after_request
def headers(r):
 r.headers['X-Content-Type-Options']='nosniff'; r.headers['X-Frame-Options']='DENY'; r.headers['Referrer-Policy']='same-origin'; return r
@app.route('/login',methods=['GET','POST'])
def login():
 if request.method=='POST':
  if secrets.compare_digest(request.form.get('password',''),PASSWORD): session.clear(); session['auth']=True; return redirect('/')
  flash('Incorrect password')
 return render_template('login.html')
@app.post('/logout')
def logout(): session.clear(); return redirect('/login')
@app.get('/')
@auth
def index():
 with db() as c: rows=c.execute('SELECT * FROM bots ORDER BY created DESC').fetchall()
 items=[]
 for x in rows:
  d=dict(x)
  if d['container_id']:
   r=docker('inspect','-f','{{.State.Status}}',d['container_id'])
   if r.returncode==0: d['status']=r.stdout.strip()
   else: d['status']='stopped'; d['container_id']=None
  d['date']=time.strftime('%Y-%m-%d %H:%M',time.localtime(d['created'])); items.append(d)
 return render_template('index.html',bots=items)
@app.post('/upload')
@auth
def upload():
 f=request.files.get('botfile')
 if not f or not f.filename.lower().endswith('.py'): flash('Select a .py file'); return redirect('/')
 bid=secrets.token_hex(8); folder=UP/bid; folder.mkdir(mode=0o700); f.save(folder/'main.py'); os.chmod(folder/'main.py',0o600)
 req=request.form.get('requirements','').strip(); lines=[z.strip() for z in req.splitlines() if z.strip() and not z.strip().startswith('#')]
 if len(lines)>30 or any(not re.fullmatch(r'[A-Za-z0-9_.-]+([<>=!~]{1,2}[A-Za-z0-9.*+_.-]+)?',z) for z in lines): shutil.rmtree(folder,ignore_errors=True); flash('Use simple package names, one per line'); return redirect('/')
 env={}
 for k,v in request.form.items():
  if k.startswith('env_key_') and v.strip():
   key=v.strip(); val=request.form.get('env_val_'+k[8:],'')
   if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}',key) or len(val)>2048: flash('Invalid environment variable'); return redirect('/')
   env[key]=val
 with db() as c: c.execute('INSERT INTO bots VALUES(?,?,?,?,?,?,?,?)',(bid,(request.form.get('name') or Path(secure_filename(f.filename)).stem)[:80],secure_filename(f.filename),None,'uploaded',time.time(),json.dumps(env),'\n'.join(lines)))
 flash('Uploaded. Review code before starting.'); return redirect('/')
@app.post('/start/<i>')
@auth
def start(i):
 b=bot(i); folder=UP/i
 if b['container_id'] and docker('inspect',b['container_id']).returncode==0: flash('Already has a container; stop first'); return redirect('/')
 (folder/'requirements.txt').write_text(b['requirements']+'\n')
 (folder/'Dockerfile').write_text('''FROM python:3.11-slim\nENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1\nWORKDIR /app\nCOPY requirements.txt .\nRUN if [ -s requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi\nCOPY main.py .\nRUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin botuser\nUSER 10001:10001\nCMD ["python","-u","/app/main.py"]\n''')
 image='tgbot-image:'+i; name='tgbot-'+i; r=docker('build','--pull','-t',image,str(folder))
 if r.returncode: flash('Build failed: '+(r.stderr[-500:] or r.stdout[-500:])); return redirect('/')
 args=['run','-d','--name',name,'--restart','unless-stopped','--memory','256m','--memory-swap','256m','--cpus','0.50','--pids-limit','64','--read-only','--tmpfs','/tmp:rw,noexec,nosuid,size=16m','--cap-drop','ALL','--security-opt','no-new-privileges:true']
 for k,v in json.loads(b['env']).items(): args+=['-e',f'{k}={v}']
 r=docker(*args,image)
 if r.returncode: flash('Start failed: '+r.stderr[-500:]); return redirect('/')
 with db() as c: c.execute("UPDATE bots SET container_id=?,status='running' WHERE id=?",(r.stdout.strip(),i))
 flash('Bot started'); return redirect('/')
@app.post('/stop/<i>')
@auth
def stop(i):
 b=bot(i)
 if b['container_id']: docker('stop','-t','5',b['container_id']); docker('rm',b['container_id'])
 with db() as c: c.execute("UPDATE bots SET container_id=NULL,status='stopped' WHERE id=?",(i,))
 return redirect('/')
@app.get('/logs/<i>')
@auth
def logs(i):
 b=bot(i)
 if not b['container_id']: return jsonify(logs='No logs yet')
 r=docker('logs','--tail','150',b['container_id']); return jsonify(logs=(r.stdout+r.stderr)[-20000:])
@app.post('/delete/<i>')
@auth
def delete(i):
 b=bot(i)
 if b['container_id']: docker('rm','-f',b['container_id'])
 docker('image','rm','-f','tgbot-image:'+i); shutil.rmtree(UP/i,ignore_errors=True)
 with db() as c: c.execute('DELETE FROM bots WHERE id=?',(i,))
 return redirect('/')
if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.getenv('PORT','8080')))
