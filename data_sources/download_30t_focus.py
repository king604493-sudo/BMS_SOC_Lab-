#!/usr/bin/env python3
"""Download only same-cell 25degC Samsung 30T C20 and dynamic tests.
"""
from pathlib import Path
import argparse,json,urllib.error,urllib.request,hashlib,sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from fetch_mendeley import fetch_json,download_file,API,HEADERS

def index_files(key,version):
    roots=[f'{API}/datasets/{key}/files?version={version}',
           f'{API}/datasets/{key}?version={version}']
    issues=[]
    for endpoint in roots:
        try:
            js=fetch_json(endpoint)
            files=js if isinstance(js,list) else js.get('files') or js.get('results') or js.get('items') or []
            if files:return files,endpoint
        except Exception as e:
            issues.append(str(e))
    raise RuntimeError('Mendeley metadata request failed: '+'; '.join(issues))

def run():
    ap=argparse.ArgumentParser()
    ap.add_argument('--output',default=str(Path(__file__).resolve().parents[1]/'datasets/30t_measured'))
    ap.add_argument('--version',type=int,default=2)
    args=ap.parse_args()
    root=Path(args.output).expanduser().resolve();root.mkdir(parents=True,exist_ok=True)
    try:files,endpoint=index_files('9xyvy2njj3',args.version)
    except Exception as exc:
        print('Automatic Mendeley download unavailable:',exc)
        print('Manual:',f'https://data.mendeley.com/datasets/9xyvy2njj3/{args.version}')
        return 2
    (root/'index.json').write_text(json.dumps(files,indent=2,ensure_ascii=False))
    # One slow-charge/discharge and one >1h mixed drive, same research cell, 25C.
    choices=[]
    named=[f for f in files if '25degc' in str(f.get('filename') or f.get('name') or '').lower()]
    candidates=[('C20DisCh',lambda s:'c20disch' in s),
                ('Mixed',lambda s:'mixed' in s),
                ('UDDS',lambda s:'udds' in s)]
    for label,pred in candidates:
        xs=[f for f in named if pred(str(f.get('filename') or f.get('name') or '').lower())]
        if xs:choices.append((label, xs[0]))
    if not any(x[0]=='C20DisCh' for x in choices):
        print('C/20 25degC not found; inspect index.json and download it manually.');return 2
    if not any(x[0] in ('Mixed','UDDS') for x in choices):
        print('Drive 25degC not found; inspect index.json and download it manually.');return 2
    success=True
    for label,item in choices[:2]:
        name=Path(item.get('filename') or item.get('name') or label+'.mat').name
        content=item.get('content_details') or {}
        url=content.get('download_url') or item.get('download_url')
        if not url and item.get('id'):
            url=f'{API}/datasets/9xyvy2njj3/files/{item["id"]}/file_downloaded?version={args.version}'
        if not url:
            print('No URL for',name);success=False;continue
        target=root/name
        try:
            if target.exists() and target.stat().st_size>100: print('Exists:',target);continue
            print('Downloading:',name)
            n,digest=download_file(url,target,content.get('sha256_hash'))
            print('Saved:',target,n,digest)
        except Exception as exc:
            success=False;print('FAILED',name,str(exc))
    if success:
        print('Ready. Run: python data_sources/samsung30t_pipeline.py --data',root)
    return 0 if success else 2

if __name__=='__main__':raise SystemExit(run())
