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


def verify_ocr(witnesses, path, sha256, provenance):
    """Check image-space crops against pinned OCR words, retaining their raw reading.

    Recognition is not a literal-source guarantee. A committed visual review and
    independent publisher transcription authorize any corrected source quotation.
    """
    if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=sha256:
        raise ValueError('Held PDF bytes changed')
    return ocr_quotes(witnesses,provenance)


def ocr_quotes(witnesses, provenance):
    """Reproduce raw crops from immutable recognition evidence, without file access."""
    pages=provenance['pages'];quotes={}
    for key,witness in witnesses.items():
        page,box=rectangle(witness)
        if page>len(pages):raise ValueError('OCR witness page is absent: '+key)
        words=[w['text'] for w in pages[page-1]['words'] if
               box[0]<=(w['box'][0]+w['box'][2])/2<=box[2] and
               box[1]<=(w['box'][1]+w['box'][3])/2<=box[3]]
        quote=normalized(' '.join(words))
        if not quote or quote!=normalized(witness.get('text')):
            raise ValueError('OCR witness quotation changed: '+key)
        quotes[key]=quote
    return quotes


def reviewed_quote(proof, key):
    correction=proof.get('ocr_corrections',{}).get(key)
    return normalized(correction['after'] if correction else proof['witnesses'][key]['text'])


def corroborated_quotes(proof, quotes, transcription):
    """Apply only explicit, source-bound corrections present in City-authored text."""
    import difflib
    import re
    def comparable(text):
        text=re.sub(r'\bpercent\b','%',text.casefold())
        return ''.join(text.split()).replace('–','-').replace('—','-')
    def characters(text):
        return re.sub(r'[^a-z0-9]','',text.casefold())
    name_keys=set()
    price_keys=set()
    header_keys=set()
    for item in [*proof.get('participants',[]),*proof.get('responses',[])]:
        name_keys.update(item.get('name_witnesses') or ([item['name_witness']] if item.get('name_witness') else []))
        price_keys.update(v['value_witness'] for v in item.get('prices',[]))
        header_keys.update(v['header_witness'] for v in item.get('prices',[]))
    result=dict(quotes)
    for key,correction in proof.get('ocr_corrections',{}).items():
        if (key not in quotes or quotes[key]!=normalized(correction['before']) or
                not correction.get('reason') or not normalized(correction['after']) or
                comparable(correction['after']) not in comparable(transcription)):
            raise ValueError('OCR correction lacks its original reading or publisher corroboration')
        before,after=correction['before'],correction['after']
        a,b=characters(before),characters(after)
        # Source review cannot use recognition cleanup to rename firms, exchange
        # their prices, or repair a number. Noncritical text corrections remain
        # small, explicit and independently corroborated.
        if (re.findall(r'\d',before)!=re.findall(r'\d',after) or
                (key in name_keys|price_keys and a!=b) or
                any(characters(quotes[n]) in a and characters(quotes[n]) not in b for n in name_keys)):
            raise ValueError('OCR correction cannot change supplier identity or price digits')
        edits=sum(max(i2-i1,j2-j1) for op,i1,i2,j1,j2 in difflib.SequenceMatcher(None,a,b).get_opcodes() if op!='equal')
        if edits>(10 if key in header_keys else 2):
            raise ValueError('OCR correction changes more than a small recognition error')
        result[key]=normalized(correction['after'])
    return result
