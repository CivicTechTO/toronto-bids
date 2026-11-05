"""Verify reviewed rectangular source witnesses against held PDF words.

Coordinates are source evidence, not a table parser: semantic row/column roles live
in committed reviews. A whole PDF is read once; every crop must reproduce its quote.
"""
from functools import lru_cache
import hashlib
import math
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET


def normalized(text):
    return ' '.join((text or '').split())


def rectangle(area):
    page=area.get('page');box=area.get('box')
    if type(page) is not int or page<1 or not isinstance(box,list) or len(box)!=4:
        raise ValueError('Invalid PDF witness page/rectangle')
    if not all(type(v) in (int,float) and math.isfinite(v) for v in box):
        raise ValueError('Non-finite PDF witness rectangle')
    x0,y0,x1,y1=box
    if min(box)<0 or x1<=x0 or y1<=y0:raise ValueError('Empty PDF witness rectangle')
    return page,box


def contains(outer,inner):
    op,ob=rectangle(outer);ip,ib=rectangle(inner)
    return op==ip and ob[0]<=ib[0]+1e-6 and ob[1]<=ib[1]+1e-6 and ob[2]+1e-6>=ib[2] and ob[3]+1e-6>=ib[3]


def in_areas(witness,areas):
    return any(contains(area,witness) for area in areas)


@lru_cache(maxsize=16)
def _pages(path,sha256):
    # sha256 is part of the cache key; the caller checks original bytes first.
    result=subprocess.run(['pdftotext','-bbox-layout',str(path),'-'],check=True,capture_output=True)
    root=ET.fromstring(result.stdout)
    pages=[]
    for page in root.iter():
        if page.tag.rsplit('}',1)[-1]!='page':continue
        lines=[]
        for line in page.iter():
            if line.tag.rsplit('}',1)[-1]!='line':continue
            words=[]
            for word in line:
                if word.tag.rsplit('}',1)[-1]=='word':
                    words.append(([float(word.attrib[k]) for k in ('xMin','yMin','xMax','yMax')],''.join(word.itertext())))
            if words:lines.append(words)
        pages.append((float(page.attrib['width']),float(page.attrib['height']),lines))
    return pages


def verify(witnesses,path,sha256):
    """Validate all source crops and return their normalized literal quotations."""
    path=Path(path)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=sha256:raise ValueError('Held PDF bytes changed')
    pages=_pages(str(path),sha256);quotes={}
    for key,witness in witnesses.items():
        page,box=rectangle(witness)
        if page>len(pages):raise ValueError('PDF witness page is absent: '+key)
        width,height,lines=pages[page-1]
        if box[2]>width+.01 or box[3]>height+.01:raise ValueError('PDF witness outside page: '+key)
        selected=[]
        for line in lines:
            words=[(b,t) for b,t in line if box[0]<=(b[0]+b[2])/2<=box[2] and box[1]<=(b[1]+b[3])/2<=box[3]]
            if words:selected.append((min(b[1] for b,t in words),min(b[0] for b,t in words),' '.join(t for b,t in sorted(words,key=lambda w:w[0][0]))))
        quote=normalized(' '.join(t for y,x,t in sorted(selected)))
        if not quote or quote!=normalized(witness.get('text')):raise ValueError('PDF witness quotation changed: '+key)
        quotes[key]=quote
    return quotes
