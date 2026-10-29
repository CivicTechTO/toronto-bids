"""Publish the held SQLite artifact through R2's S3 multipart interface.

Cloudflare documents the S3 key ID as the API token ID and its secret as the
SHA-256 of that token. Credentials stay in memory; no separate S3 secret is
provisioned. The publisher must keep the source file stable during transfer.
"""
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
import httpx


def _token_id(http, account, token):
    for scope in (f'accounts/{account}', 'user'):
        response = http.get(f'https://api.cloudflare.com/client/v4/{scope}/tokens/verify',
            headers={'Authorization':'Bearer ' + token})
        if response.status_code in (401,403,404):
            continue  # Account and legacy user tokens have separate verify routes.
        if response.status_code != 200:
            raise ValueError(f'Cloudflare token verification failed (HTTP {response.status_code})')
        payload = response.json()
        result = payload.get('result') or {}
        if (not payload.get('success') or result.get('status') != 'active'
                or not re.fullmatch('[a-f0-9]{32}', str(result.get('id','')))):
            raise ValueError('Cloudflare verification did not return an active token')
        return result['id']
    raise ValueError('Cloudflare token verification failed for account and user scopes')


def mirror_database(path, *, bucket, account_id, api_token, http=None, s3_factory=boto3.client):
    """Upload one stable SQLite file; verify authenticated size and content fingerprint."""
    if not re.fullmatch('[a-f0-9]{32}', account_id or ''):
        raise ValueError('CLOUDFLARE_ACCOUNT_ID must be a Cloudflare account identifier')
    if not re.fullmatch('[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket or ''):
        raise ValueError('Invalid R2 bucket name')
    if not api_token:
        raise ValueError('CLOUDFLARE_API_TOKEN is required')
    path = Path(path)
    before = path.stat()
    with path.open('rb') as source:
        if source.read(16) != b'SQLite format 3\0':
            raise ValueError('R2 mirror input is not a SQLite database')
        source.seek(0)
        digest = hashlib.file_digest(source,'sha256').hexdigest()
    owned = http is None
    http = http or httpx.Client(timeout=30,follow_redirects=False)
    storage = None
    try:
        ident = _token_id(http,account_id,api_token)
        storage = s3_factory('s3',endpoint_url=f'https://{account_id}.r2.cloudflarestorage.com',
            region_name='auto',aws_access_key_id=ident,
            aws_secret_access_key=hashlib.sha256(api_token.encode()).hexdigest(),
            config=Config(retries={'mode':'standard','max_attempts':3},
                connect_timeout=30,read_timeout=120))
        storage.upload_file(str(path),bucket,'bids.sqlite',
            ExtraArgs={'ContentType':'application/x-sqlite3','Metadata':{'sha256':digest}},
            Config=TransferConfig(multipart_threshold=8*1024**2,
                multipart_chunksize=32*1024**2,max_concurrency=2))
        after = path.stat()
        if (after.st_size,after.st_mtime_ns,after.st_ino) != (before.st_size,before.st_mtime_ns,before.st_ino):
            raise ValueError('R2 mirror input changed during upload')
        held = storage.head_object(Bucket=bucket,Key='bids.sqlite')
        if held['ContentLength'] != before.st_size:
            raise ValueError('R2 mirror size verification failed')
        if held.get('Metadata',{}).get('sha256') != digest:
            raise ValueError('R2 mirror fingerprint verification failed')
        return dict(bucket=bucket,key='bids.sqlite',bytes=before.st_size,sha256=digest,verified=True)
    finally:
        if storage is not None and hasattr(storage,'close'):
            storage.close()
        if owned:
            http.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Mirror the held SQLite artifact to R2')
    parser.add_argument('--file',required=True,type=Path)
    parser.add_argument('--bucket',default=os.environ.get('TB_R2_BUCKET','toronto-bids-data'))
    args = parser.parse_args(argv)
    try:
        result = mirror_database(args.file,bucket=args.bucket,
            account_id=os.environ.get('CLOUDFLARE_ACCOUNT_ID'),
            api_token=os.environ.get('CLOUDFLARE_API_TOKEN'))
    except Exception as exc:
        print(f'R2 mirror failed: {exc}',file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
