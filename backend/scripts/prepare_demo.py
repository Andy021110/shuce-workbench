"""Prepare reproducible demo data without model calls.

Run from backend: python scripts/prepare_demo.py [--download-olist | --olist-archive PATH]
"""
from pathlib import Path
import argparse
import subprocess
import sys
import tempfile
from urllib.request import urlopen
import shutil

ROOT = Path(__file__).resolve().parents[1]


def run(script: str, *args: str) -> None:
    subprocess.run([sys.executable, str(ROOT/'scripts'/script), *args], cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--download-olist', action='store_true')
    group.add_argument('--olist-archive', type=Path)
    args = parser.parse_args()
    if not (ROOT/'data/databases/short_video_ops/_schema.json').exists():
        run('generate_short_video_ops_data.py')
        run('generate_short_video_ops_schema.py')
    else:
        print('Synthetic dataset already prepared; preserved existing files.')
    target = ROOT/'data/databases/olist_ecommerce/_schema.json'
    if args.download_olist or args.olist_archive:
        if target.exists():
            print('Olist already installed; preserved existing files.')
        elif args.olist_archive:
            run('import_olist.py', str(args.olist_archive.expanduser().resolve()))
        else:
            url = 'https://www.kaggle.com/api/v1/datasets/download/olistbr/brazilian-ecommerce'
            with tempfile.TemporaryDirectory(prefix='shuce-olist-') as directory:
                archive = Path(directory)/'olist.zip'
                print('Downloading public Olist archive; import verifies the pinned SHA-256.')
                with urlopen(url, timeout=180) as response, archive.open('wb') as output:
                    shutil.copyfileobj(response, output)
                run('import_olist.py', str(archive))
    elif not target.exists():
        print('Olist is not installed. For ecommerce reviews run with --download-olist.')
    print('Demo data ready. No model services were called.')


if __name__ == '__main__':
    main()
