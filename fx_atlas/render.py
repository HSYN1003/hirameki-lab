"""Build one self-contained HTML and one atomic public JSON snapshot."""
from pathlib import Path
import argparse, html, json, shutil
from .core import *

def e(x):return html.escape(str(x if x is not None else '未取得'),quote=True)
def render_static(s):
    paragraphs=''.join('<p>'+e(x)+'</p>' for x in s['summary']['paragraphs'])
    trs=''.join('<tr><th>'+e(p['pair'])+'</th><td>'+e(p['short']['label'])+'</td><td>'+e(p['investors']['label'])+'</td><td>'+e(p['synthesis'])+'</td></tr>' for p in s['summary']['pairs'])
    health=''.join('<tr><th>'+e(v.get('name',k))+'</th><td>'+e(v.get('status'))+'</td><td>'+e(v.get('newest_observation'))+'</td><td>'+e(v.get('error'))+'</td></tr>' for k,v in s['sources'].items())
    return '<section class="panel"><h2>総合サマリー</h2>'+paragraphs+'</section><section class="panel wide"><h2>10ペア</h2><div class="scroll"><table><tr><th>ペア</th><th>短期</th><th>投資家</th><th>読み合わせ</th></tr>'+trs+'</table></div></section><section class="panel wide"><h2>収集状態</h2><div class="scroll"><table><tr><th>ソース</th><th>状態</th><th>資料日</th><th>問題</th></tr>'+health+'</table></div></section>'

def build(root:Path):
    data=root/'fx-sentiment-data';s=json.loads((data/'snapshot.json').read_text())
    a=json.loads((data/'previous_snapshot.json').read_text()) if (data/'previous_snapshot.json').exists() else {}
    template=(root/'fx_atlas'/'template.html').read_text()
    embed=lambda o:json.dumps(o,ensure_ascii=False,allow_nan=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    text=template.replace('__DATA__',embed(s)).replace('__ARCHIVE__',embed(a)).replace('__INITIAL__',render_static(s))
    (root/'fx-sentiment.html').write_text(text,encoding='utf-8')
    return root/'fx-sentiment.html'

def public_site(root:Path,destination:Path):
    """Preserve existing public static site files, not source code, state or untrusted symlinks."""
    if destination.exists():shutil.rmtree(destination)
    destination.mkdir(parents=True)
    suffixes={'.html','.css','.js','.png','.jpg','.jpeg','.svg','.ico','.webp','.woff','.woff2','.webmanifest','.xml','.txt'}
    blocked={'fx_atlas','tests','audit','private-raw','fx-sentiment-data','__pycache__','node_modules','_site'}
    for p in root.rglob('*'):
        rel=p.relative_to(root)
        if any(part.startswith('.') or part in blocked for part in rel.parts):continue
        if p.is_symlink() or not p.is_file() or p.suffix.lower() not in suffixes:continue
        out=destination/rel;out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,out)
    for name in ('snapshot.json','summary.txt','previous_snapshot.json'):
        p=root/'fx-sentiment-data'/name
        if p.exists():
            out=destination/'fx-sentiment-data'/name;out.parent.mkdir(exist_ok=True);shutil.copy2(p,out)
    (destination/'.nojekyll').write_text('')
    if not (destination/'index.html').exists():
        (destination/'index.html').write_text('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=fx-sentiment.html"><a href="fx-sentiment.html">通貨心理レーダー</a>')

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--root',type=Path,default=Path('.'));a.add_argument('--site',type=Path)
    args=a.parse_args();print(build(args.root))
    if args.site:public_site(args.root,args.site)
