import csv, os, re, threading, unicodedata
from urllib.parse import quote
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from playwright.sync_api import sync_playwright
from rapidfuzz.fuzz import token_set_ratio

BASE='https://historia-hispanica.rah.es'; SEARCH=BASE+'/busqueda?q={q}&tipo=0'
STOP={'de','del','la','las','los','y','el','don','dona','fray','sor'}

def norm(s):
    s=unicodedata.normalize('NFKD',str(s or '')); s=''.join(c for c in s if not unicodedata.combining(c))
    return ' '.join(re.sub(r'[^a-zA-Z0-9 ]+',' ',s.lower()).split())
def toks(s): return [x for x in norm(s).split() if x not in STOP and len(x)>1]
def score(a,b): return token_set_ratio(' '.join(toks(a)),' '.join(toks(b)))
def personlike(name):
    t=toks(name); bad={'abad','agente','duque','marques','conde','rey','reina','obispo','arzobispo','ministro'}
    return len(t)>=2 and not any(x in bad for x in t[:1])
def variants(name):
    raw=' '.join(str(name).split()); t=toks(raw); out=[raw]
    if len(t)>=3: out += [' '.join(t), t[0]+' '+t[-1], ' '.join(t[-2:])]
    elif len(t)==2: out += [t[-1]]
    seen=[]
    for x in out:
        if norm(x) and norm(x) not in [norm(y) for y in seen]: seen.append(x)
    return seen[:4]

def read_csv(path):
    raw=open(path,'rb').read()
    for enc in ('utf-8-sig','cp1252','latin1'):
        try:
            text=raw.decode(enc); dialect=csv.Sniffer().sniff(text[:5000], delimiters=';,\t,')
            rows=list(csv.DictReader(text.splitlines(), dialect=dialect))
            if rows:return rows,list(rows[0].keys())
        except Exception:pass
    raise ValueError('No pude interpretar el CSV.')

def search_once(page,q,name,seen,out):
    page.goto(SEARCH.format(q=quote(q)),wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(2200)
    links=page.locator('a[href*="/biografias/"]')
    for i in range(min(links.count(),60)):
        a=links.nth(i); href=a.get_attribute('href') or ''; txt=(a.inner_text() or '').strip()
        if not href or not txt:continue
        if href.startswith('/'):href=BASE+href
        if href in seen:continue
        seen.add(href); out.append((txt,href,score(name,txt),q))

def candidates(page,name):
    out=[]; seen=set()
    for q in variants(name):
        search_once(page,q,name,seen,out)
        if out and max(x[2] for x in out)>=96:break
    return sorted(out,key=lambda x:x[2],reverse=True)

def extract(page,url):
    page.goto(url,wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(1000)
    body=page.locator('body').inner_text(); lines=[x.strip() for x in body.splitlines() if x.strip()]
    title=''
    for sel in ('h1','h2'):
        loc=page.locator(sel)
        if loc.count(): title=(loc.first.inner_text() or '').strip()
        if title:break
    return title,' '.join(lines[:12])[:2200],body

class App:
 def __init__(self,root):
    self.root=root; root.title('Jovellanos · Historia Hispánica v2'); root.geometry('950x650'); self.path=None; self.rows=[]; self.fields=[]
    ttk.Label(root,text='Jovellanos · Historia Hispánica · v2',font=('Segoe UI',18,'bold')).pack(pady=(18,4)); ttk.Label(root,text='Búsqueda ampliada y control conservador de coincidencias').pack()
    top=ttk.Frame(root); top.pack(fill='x',padx=20,pady=16); ttk.Button(top,text='1. Cargar CSV',command=self.load).pack(side='left'); self.filelab=ttk.Label(top,text='Ningún archivo cargado'); self.filelab.pack(side='left',padx=12)
    opts=ttk.Frame(root); opts.pack(fill='x',padx=20); ttk.Label(opts,text='Columna de nombres:').grid(row=0,column=0); self.col=ttk.Combobox(opts,state='readonly',width=32); self.col.grid(row=0,column=1,padx=8)
    ttk.Label(opts,text='Filas a procesar (0 = todas):').grid(row=0,column=2,padx=(20,0)); self.limit=tk.StringVar(value='20'); ttk.Entry(opts,textvariable=self.limit,width=8).grid(row=0,column=3,padx=8)
    self.full=tk.BooleanVar(value=True); ttk.Checkbutton(root,text='Extraer texto de la ficha en coincidencias automáticas',variable=self.full).pack(anchor='w',padx=20,pady=10); ttk.Button(root,text='2. CONSULTAR HISTORIA HISPÁNICA',command=self.start).pack(pady=8)
    self.pb=ttk.Progressbar(root,mode='determinate'); self.pb.pack(fill='x',padx=20,pady=6); self.status=ttk.Label(root,text='Listo.'); self.status.pack(anchor='w',padx=20)
    self.tree=ttk.Treeview(root,columns=('original','estado','rah','score'),show='headings',height=18)
    for c,t,w in [('original','Nombre CSV',280),('estado','Estado',160),('rah','Nombre RAH',300),('score','Coincidencia',100)]:self.tree.heading(c,text=t);self.tree.column(c,width=w)
    self.tree.pack(fill='both',expand=True,padx=20,pady=12)
 def load(self):
    p=filedialog.askopenfilename(filetypes=[('CSV','*.csv')]);
    if not p:return
    try:self.rows,self.fields=read_csv(p)
    except Exception as e:messagebox.showerror('CSV',str(e));return
    self.path=p;self.filelab.config(text=f'{os.path.basename(p)} · {len(self.rows)} filas');self.col['values']=self.fields;self.col.set('Nombre_literal' if 'Nombre_literal' in self.fields else self.fields[0])
 def start(self):
    if not self.path:return messagebox.showwarning('Falta CSV','Carga primero un CSV.')
    threading.Thread(target=self.run,daemon=True).start()
 def run(self):
    try:
      n=int(self.limit.get() or 0);work=self.rows if n<=0 else self.rows[:n];namecol=self.col.get();results=[];self.pb['maximum']=len(work);self.tree.delete(*self.tree.get_children())
      with sync_playwright() as p:
       try:browser=p.chromium.launch(channel='msedge',headless=True)
       except Exception:browser=p.chromium.launch(channel='chrome',headless=True)
       page=browser.new_page()
       for i,row in enumerate(work,1):
        name=row.get(namecol,'');self.status.config(text=f'{i}/{len(work)} · {name}');rec=dict(row);rec.update({'RAH_estado':'','RAH_nombre':'','RAH_url':'','RAH_score':'','RAH_busqueda_usada':'','RAH_num_candidatos':'','RAH_titulo_ficha':'','RAH_resumen':'','RAH_texto_biografia':'','RAH_error':''})
        try:
         cs=candidates(page,name);rec['RAH_num_candidatos']=len(cs);best=cs[0] if cs else None
         if not best or best[2]<60:state='NO_ENCONTRADO';best=None
         elif best[2]>=90 and personlike(name):state='COINCIDENCIA_AUTO'
         else:state='REVISAR'
         rec['RAH_estado']=state
         if best:
          rec['RAH_nombre'],rec['RAH_url'],rec['RAH_score'],rec['RAH_busqueda_usada']=best
          if self.full.get() and state=='COINCIDENCIA_AUTO':rec['RAH_titulo_ficha'],rec['RAH_resumen'],rec['RAH_texto_biografia']=extract(page,best[1])
        except Exception as e:rec['RAH_estado']='ERROR';rec['RAH_error']=str(e)[:500]
        results.append(rec);self.tree.insert('','end',values=(name,rec['RAH_estado'],rec['RAH_nombre'],rec['RAH_score']));self.pb['value']=i
       browser.close()
      out=os.path.splitext(self.path)[0]+'_RAH_enriquecido_v2.csv';fields=list(results[0].keys()) if results else self.fields
      with open(out,'w',newline='',encoding='utf-8-sig') as f:w=csv.DictWriter(f,fieldnames=fields,delimiter=';');w.writeheader();w.writerows(results)
      self.status.config(text='Terminado: '+out);messagebox.showinfo('Terminado','CSV enriquecido guardado en:\n'+out)
    except Exception as e:messagebox.showerror('Error',str(e));self.status.config(text='Error: '+str(e))
if __name__=='__main__':root=tk.Tk();App(root);root.mainloop()
