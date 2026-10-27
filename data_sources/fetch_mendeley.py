#!/usr/bin/env python3
"""Download public Mendeley datasets, using the API endpoint current since 2024-10.

Usage:
  python3 fetch_mendeley.py --output /Volumes/SSD/BMS_SOC_Lab/datasets --dataset 30t_dynamic

This only downloads raw source files; MATLAB .mat conversion still requires checking the
original variable definitions and discharge/charge sign convention.
"""
import argparse
import hashlib
import json
import pathlib
import urllib.error
import urllib.parse
import urllib.request

DATASETS = {
    '30t_dynamic': ('h3yfxtwkjz', 1),
    '30t_ocv': ('m9w7grpjc7', 1),
    '30t_drive': ('9xyvy2njj3', 2),
}
API = 'https://api.data.mendeley.com'
HEADERS = {'User-Agent': 'BMS-SOC-Lab/0.2 (public research dataset download)', 'Accept': 'application/json'}


def fetch_json(url):
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def download_file(url, path, sha256=None):
    request = urllib.request.Request(url, headers={'User-Agent': HEADERS['User-Agent']})
    temp = path.with_name(path.name + '.part')
    size = 0
    digest = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=120) as inp, open(temp, 'wb') as out:
        while True:
            chunk = inp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    if sha256 and digest.hexdigest().lower() != sha256.lower():
        temp.unlink(missing_ok=True)
        raise RuntimeError('SHA256 mismatch: ' + path.name)
    temp.replace(path)
    return size, digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--dataset', choices=list(DATASETS) + ['all'], default='all')
    args = parser.parse_args()
    root = pathlib.Path(args.output).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    keys = list(DATASETS) if args.dataset == 'all' else [args.dataset]
    ok = True
    for key in keys:
        short_id, version = DATASETS[key]
        folder = root / key
        folder.mkdir(parents=True, exist_ok=True)
        endpoint = f'{API}/datasets/{short_id}/files?version={version}'
        try:
            files = fetch_json(endpoint)
            if isinstance(files, dict):
                files = files.get('files') or files.get('results') or files.get('items') or []
            if not isinstance(files, list):
                raise TypeError('Unexpected metadata shape: ' + repr(type(files)))
            (folder / 'source_metadata.json').write_text(json.dumps(files, indent=2, ensure_ascii=False), encoding='utf-8')
            print(f'[{key}] indexed {len(files)} files')
            for item in files:
                content = item.get('content_details') or {}
                name = pathlib.Path(item.get('filename') or item.get('name') or (str(item.get('id')) + '.bin')).name
                href = content.get('download_url') or item.get('download_url')
                if not href and item.get('id'):
                    href = f'{API}/datasets/{short_id}/files/{item["id"]}/file_downloaded?version={version}'
                if not href:
                    print('  [SKIP] missing download link:', name)
                    ok = False
                    continue
                target = folder / name
                if target.exists() and target.stat().st_size > 0:
                    print('  [EXISTS]', target)
                    continue
                size, digest = download_file(href, target, content.get('sha256_hash'))
                print(f'  [SAVED] {target} ({size} bytes, sha256 {digest[:16]}...)')
        except (urllib.error.URLError, TimeoutError, RuntimeError, OSError, ValueError, TypeError) as error:
            ok = False
            print(f'[{key}] FAILED: {type(error).__name__}: {error}')
            print('  Manual download:', f'https://data.mendeley.com/datasets/{short_id}/{version}')
    if not ok:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
