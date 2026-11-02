"""Deterministic, self-contained outlined SVG figures; PNG uses the same SVG."""
import argparse,fcntl,hashlib,html,json,pathlib,subprocess,sys
from functools import lru_cache
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
import catalog
ROOT=pathlib.Path(__file__).resolve().parent;W=1000;H=720
PAPER='#FFFFFF';INK='#20252B';MUTED='#555D66';RULE='#DDE1E5';TEAL='#176D83';RUST='#A35D40';PALE='#E5E9DF';AMBER='#B98A45'
FONT_PATHS={'sans':'/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf','bold':'/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf','serif':'/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf'}
@lru_cache(None)
def font(name):
 f=TTFont(FONT_PATHS[name]);kern={}
 if 'kern' in f:
  for table in f['kern'].kernTables:kern.update(table.kernTable)
 return f,f.getGlyphSet(),f.getBestCmap(),f['head'].unitsPerEm,kern
@lru_cache(None)
def glyph(name,char):
 f,gs,cmap,upem,kern=font(name);g=cmap.get(ord(char),'.notdef');pen=SVGPathPen(gs);gs[g].draw(pen);return g,pen.getCommands(),f['hmtx'][g][0]
def width(text,size=22,name='sans'):
 _,_,_,upem,kern=font(name);total=0;previous=None
 for c in text:
  g,d,advance=glyph(name,c);total+=kern.get((previous,g),0)+advance;previous=g
 return total*size/upem
def dimensions(spec):return catalog.dimensions(spec)
class Canvas:
 def __init__(self,s):self.s=s;self.body=[];self.defs={};self.w,self.h=dimensions(s);self.text_boxes=[]
 def rect(self,x,y,w,h,fill,rx=0,stroke=None,sw=1):self.body.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}"'+(f' stroke="{stroke}" stroke-width="{sw}"' if stroke else '')+'/>')
 def line(self,x1,y1,x2,y2,color=RULE,sw=1,dash=None):self.body.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{sw}"'+(f' stroke-dasharray="{dash}"' if dash else '')+'/>')
 def circle(self,x,y,r,fill,stroke=None,sw=1):self.body.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{fill}"'+(f' stroke="{stroke}" stroke-width="{sw}"' if stroke else '')+'/>')
 def text(self,x,y,text,size=22,name='sans',fill=INK,anchor='start'):
  text=str(text);advance=0;previous=None;_,_,_,upem,kern=font(name);scale=size/upem
  if anchor=='middle':x-=width(text,size,name)/2
  elif anchor=='end':x-=width(text,size,name)
  self.text_boxes.append((x,y,width(text,size,name),size))
  items=[]
  for char in text:
   g,d,a=glyph(name,char);advance+=kern.get((previous,g),0);key=f'g-{name}-{ord(char)}';self.defs[key]=d
   if d:items.append(f'<use href="#{key}" transform="translate({advance} 0)"/>')
   advance+=a;previous=g
  self.body.append(f'<g aria-label="{html.escape(text,quote=True)}" fill="{fill}" transform="translate({x:.3f} {y}) scale({scale:.7f} {-scale:.7f})">'+''.join(items)+'</g>')
 def wrap(self,x,y,text,maxwidth,size=20,name='sans',fill=MUTED,leading=29):
  words=text.split();line=''
  for word in words:
   candidate=(line+' '+word).strip()
   if line and width(candidate,size,name)>maxwidth:self.text(x,y,line,size,name,fill);y+=leading;line=word
   else:line=candidate
  if line:self.text(x,y,line,size,name,fill);y+=leading
  return y
 def header(self):
  self.rect(0,0,W,H,PAPER)
  self.wrap(40,49,self.s['title'],920,28,'bold',INK,35)
  self.wrap(40,88,self.s['subtitle'],920,21,'sans',MUTED,27)
 def footer(self,note):
  self.line(40,636,960,636)
  self.wrap(40,670,note,920,20,'sans',MUTED,27)
 def svg(self):
  defs=''.join(f'<path id="{k}" d="{v}"/>' for k,v in sorted(self.defs.items()))
  meta=html.escape(json.dumps({'schemaVersion':1,'figureId':self.s['id'],'caption':self.s['caption'],'caveats':self.s['caveats'],'sources':self.s['sources']},sort_keys=True))
  return f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" viewBox="0 0 {self.w} {self.h}" role="img" aria-labelledby="figure-title figure-desc"><title id="figure-title">{html.escape(self.s["title"])}</title><desc id="figure-desc">{html.escape(self.s["caption"])}</desc><metadata>{meta}</metadata><defs>{defs}</defs>'+''.join(self.body)+'</svg>'
def paired(c):
 d=c.s['data'];den=d['denominator'];x0=310;x1=744;y0=130;step=73
 c.circle(41,39,8,PAPER,INK,2);c.text(60,48,'Current agent',26)
 c.rect(360,31,16,16,TEAL);c.text(389,48,'Agent + runbook',26)
 for tick in range(0,den+1,max(1,den//5)):
  x=x0+(x1-x0)*tick/den;c.line(x,94,x,665);c.text(x,706,str(tick),25,'sans',MUTED,'middle')
 for i,r in enumerate(d['rows']):
  y=y0+i*step;c.text(32,y+9,r['label'],25)
  xa=x0+(x1-x0)*r['a']/den;xb=x0+(x1-x0)*r['b']/den
  c.line(xa,y,xb,y,TEAL if r['b']>=r['a'] else RUST,4)
  c.circle(xa,y,9,PAPER,INK,2.5);c.rect(xb-5,y-5,10,10,TEAL)
  c.text(xa,y-18,str(r['a']),25,'sans',INK,'middle')
  if r['a']!=r['b']:c.text(xb,y-18,str(r['b']),25,'bold',TEAL,'middle')
 c.text(527,756,f'Immediate verified repairs / {den}',25,'sans',MUTED,'middle')

def budget(c):
 d=c.s['data'];cap=d['capacity'];x0=32;bw=736
 c.text(32,46,'Only 19 tokens remained for the answer',30,'bold')
 c.text(32,91,f'Full context window · 0 to {cap:,} tokens',26)
 c.text(32,128,'Gray: prompt · Blue: reply · Outline: allowance',24,'sans',MUTED)
 for i,r in enumerate(d['rows']):
  y=166+i*125;available=min(d['outputCap'],cap-r['prompt']);pw=bw*r['prompt']/cap;rw=bw*available/cap;gw=bw*r['output']/cap
  c.text(32,y,r['label'],26,'bold')
  c.rect(x0,y+20,bw,28,'#F1F3F5');c.rect(x0,y+20,pw,28,'#68727C')
  c.rect(x0+pw,y+20,rw,28,PAPER,0,TEAL,1);c.rect(x0+pw,y+20,gw,28,TEAL)
  c.text(32,y+87,f"{r['prompt']:,} prompt + {r['output']} response tokens",26)
 # Connector points to the true, 1.71px-wide response on the full scale.
 c.line(768,214,748,408,RUST,2,dash='5 5')
 c.rect(32,416,736,408,'#F8F9FA',12)
 c.text(56,459,'Magnified response · 19 tokens',28,'bold',RUST)
 c.text(56,501,'Enlarged for reading; not the scale above',24,'sans',MUTED)
 c.rect(56,530,192,234,PAPER,6,RUST,2)
 c.text(152,596,'19',58,'bold',RUST,'middle')
 c.text(152,638,'of 384',27,'sans',INK,'middle')
 c.text(152,675,'cap tokens',25,'sans',INK,'middle')
 c.text(152,728,'5% of cap',24,'sans',MUTED,'middle')
 c.text(280,554,'Actual saved reply',27,'bold')
 response=d.get('responseText','Saved reply not supplied')
 # Line wrapping changes no characters in the saved text.
 lines=response.replace(', ', ', \n').splitlines(keepends=True)
 for j,line in enumerate(lines):c.text(280,603+j*43,line.rstrip('\n'),25)
 c.text(280,763,'Cut off after the colon',26,'bold',RUST)
 c.text(32,884,'Budgeting produced a complete reply',28,'bold',TEAL)
 c.text(32,928,'3,303 prompt · 53 response · valid JSON',26)
 c.text(32,974,'Same 8,192 context and 384 output cap',25,'sans',MUTED)

def matrix(c):
 d=c.s['data'];den=d['denominator'];labels=d['compactLabels']
 for panel,(col,title) in enumerate(list(zip(d['focusColumns'],d['focusTitles']))):
  top=45+panel*405
  c.text(32,top,title,29,'bold',INK if panel==0 else RUST)
  c.text(32,top+41,'Preferred next actions / 5 saved states',25,'sans',MUTED)
  for j,r in enumerate(d['rows']):
   y=top+109+j*65;v=r['values'][col]
   c.text(32,y+8,labels[j],26)
   for k in range(den):c.circle(495+k*44,y,12,TEAL if k<v else PAPER,TEAL if k<v else '#A6ADB5',2)
   c.text(765,y+9,f'{v}/{den}',27,'bold',INK,'end')
 c.line(32,415,768,415)

def base_budget(c):
 d=c.s['data'];cap=d['capacity'];x0=40;bw=920
 # Outline is available output space; solid blue is actual generated output.
 c.rect(40,124,18,18,'#68727C');c.text(68,140,'Prompt',20)
 c.rect(225,124,18,18,PAPER,0,TEAL,2);c.text(253,140,'Response allowance',20)
 c.rect(565,124,18,18,TEAL);c.text(593,140,'Generated response',20)
 for i,r in enumerate(d['rows']):
  y=190+i*175;available=min(d['outputCap'],cap-r['prompt']);pw=bw*r['prompt']/cap;rw=bw*available/cap;gw=bw*r['output']/cap
  c.text(40,y,r['label'],23,'bold')
  c.rect(x0,y+25,bw,38,'#F1F3F5');c.rect(x0,y+25,pw,38,'#68727C')
  c.rect(x0+pw,y+25,rw,38,PAPER,0,TEAL,1.5);c.rect(x0+pw,y+25,gw,38,TEAL)
  c.text(40,y+100,f"{r['prompt']:,} prompt + {r['output']} generated tokens",22)
  c.text(960,y+100,'Valid JSON' if r['valid'] else 'Truncated JSON',22,'bold',TEAL if r['valid'] else RUST,'end')
  c.text(40,y+132,f'{available} response tokens available (output cap: {d["outputCap"]})',20,'sans',MUTED)
 for tick in (0,cap//2,cap):
  x=x0+bw*tick/cap;c.line(x,525,x,534,INK);c.text(x,564,f'{tick:,}',20,'sans',MUTED,'start' if tick==0 else 'end' if tick==cap else 'middle')
 c.text(500,600,'Tokens in the shared context window',20,'sans',MUTED,'middle')
 c.footer(f'Saved state {d["case"]}. History content also changed; this replay did not execute repair tools.')
def base_matrix(c):
 d=c.s['data'];xs=[405,565,725,895]
 for j,r in enumerate(d['rows']):
  labels=[('Original','history'),('Budgeted raw','history'),('Facts','time order'),('Facts','component order')]
  for k,label in enumerate(labels[j]):c.text(xs[j],125+k*27,label,20,'bold',INK,'middle')
 c.line(40,169,960,169)
 c.text(40,207,'Valid JSON / 20 states',22,'bold')
 for j,r in enumerate(d['rows']):c.text(xs[j],207,f'{r["valid"]}/20',24,'bold',TEAL if r['valid']==20 else INK,'middle')
 c.line(40,235,960,235)
 c.text(40,275,'Preferred next action / 5 states per type',22,'bold')
 for i,label in enumerate(d['columns']):
  y=331+i*55;c.text(40,y,label,22)
  for j,r in enumerate(d['rows']):
   v=r['values'][i];drop=i==len(d['columns'])-1 and j>0 and v<d['rows'][0]['values'][i]
   c.text(xs[j],y,f'{v}/{d["denominator"]}',25,'bold' if drop else 'sans',RUST if drop else INK,'middle')
  c.line(40,y+18,960,y+18)
 c.text(40,574,'Successful controls fell from 5/5 to 4/5, 4/5 and 3/5.',22,'bold',RUST)
 c.footer('Development rubric scores, not recovery rates. Only the two fact arms match available evidence.')
def render(spec):
 catalog.validate(spec);c=Canvas(spec)
 if spec['kind'] in ('system_flow','evidence_sequence'):
  from explanatory_render import draw
  draw(c)
 elif (spec['kind']=='token_budget' and 'responseText' not in spec['data']) or (spec['kind']=='matrix_counts' and 'focusColumns' not in spec['data']):
  c.header();{'token_budget':base_budget,'matrix_counts':base_matrix}[spec['kind']](c)
 else:
  c.rect(0,0,c.w,c.h,PAPER);{'paired_counts':paired,'token_budget':budget,'matrix_counts':matrix}[spec['kind']](c)
 return c.svg()
def export(identity,kind='png',scale=2):
 if kind not in ('svg','png') or scale not in (1,2,3):raise ValueError('Unsupported export')
 spec=catalog.get(identity);key=hashlib.sha256(json.dumps(spec,sort_keys=True,separators=(',',':')).encode()+pathlib.Path(__file__).read_bytes()+(ROOT/'explanatory_render.py').read_bytes()+b''.join(pathlib.Path(p).read_bytes() for p in FONT_PATHS.values())).hexdigest()[:16]
 folder=catalog.DATA/'exports'/key;folder.mkdir(parents=True,exist_ok=True)
 with (folder/'export.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX);svg=folder/(identity+'.svg')
  if not svg.exists():svg.write_text(render(spec))
  output=svg
  if kind=='png':
   output=folder/f'{identity}-{scale}x.png'
   if not output.exists():subprocess.run(['<LOCAL_HOME>/Dev/datacenter-lab/.venv/bin/python',str(ROOT/'rasterize.py'),str(svg),str(output),str(scale)],check=True,timeout=60,capture_output=True)
  manifest={'id':identity,'format':kind,'path':str(output),'sha256':catalog.sha(output),'width':dimensions(spec)[0]*(scale if kind=='png' else 1),'height':dimensions(spec)[1]*(scale if kind=='png' else 1),'renderKey':key,'sources':spec['sources'],'caption':spec['caption']};(folder/(identity+'.manifest.json')).write_text(json.dumps(manifest,indent=2));return manifest
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('action',choices=['export']);parser.add_argument('--id',required=True);parser.add_argument('--format',choices=['svg','png'],default='png');parser.add_argument('--scale',type=int,default=2);args=parser.parse_args();print(json.dumps(export(args.id,args.format,args.scale)))
