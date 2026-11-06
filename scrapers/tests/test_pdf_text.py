"""Image-only documents enter the shared extraction path without changing source bytes."""
import hashlib
import json
from pathlib import Path
import pytest
from toronto_bids import pdf_text
from toronto_bids.models import BackgroundPdf
from toronto_bids.store import db
from toronto_bids.extraction import extract_corpus, _count_uncached


def held(conn,tmp_path,text=None):
    path=tmp_path/'scan.pdf';path.write_bytes(b'%PDF-test')
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    db.upsert_row(conn,BackgroundPdf('https://example.com/scan.pdf',reference='2013.BD1.1',kind='bgrd',local_path=str(path),sha256=sha,text=text),overwrite=True);conn.commit()
    return conn.execute('SELECT * FROM background_pdf').fetchone(),path


def result(text='Supplier Alpha Ltd. $1,234.50'):
    return dict(text=text,method='tesseract',provenance=dict(version=1,engine='tesseract 5.5.0',dpi=300,psm=6,language='eng',pages=[dict(page=1,text_sha256=hashlib.sha256(text.encode()).hexdigest(),words=[dict(text='Alpha',box=[1,2,3,4],confidence=95)])]))


def test_scanned_document_reaches_normal_extraction_and_keyless_accounting(conn,tmp_path,monkeypatch):
    row,path=held(conn,tmp_path);calls=[]
    monkeypatch.setattr(pdf_text,'read_pdf',lambda p:calls.append(p) or result())
    assert _count_uncached(conn,'bd_report',{})==1
    class Client:
        def extract(self,text):
            assert text=='Supplier Alpha Ltd. $1,234.50'
            return {'contracts':[]}
    stats=extract_corpus(conn,'bd_report',client=Client(),labels={})
    assert stats['extracted']==1 and stats['no_text']==0
    assert len(calls)==1 and _count_uncached(conn,'bd_report',{})==0
    after=conn.execute('SELECT * FROM background_pdf').fetchone()
    assert dict(after)==dict(row) and path.read_bytes()==b'%PDF-test'
    proof=conn.execute('SELECT * FROM pdf_text').fetchone()
    assert proof['sha256']==row['sha256'] and proof['method']=='tesseract'
    assert json.loads(proof['provenance_json'])['pages'][0]['words'][0]['text']=='Alpha'


def test_native_text_never_invokes_ocr(conn,tmp_path,monkeypatch):
    row,_=held(conn,tmp_path,text='Original PDF text')
    monkeypatch.setattr(pdf_text,'read_pdf',lambda p:pytest.fail('native text was sent to OCR'))
    assert pdf_text.source_text(conn,row,recover=True)=='Original PDF text'
    assert conn.execute('SELECT COUNT(*) FROM pdf_text').fetchone()[0]==0


def test_changed_source_bytes_or_derived_text_refuse_replay(conn,tmp_path,monkeypatch):
    row,path=held(conn,tmp_path);monkeypatch.setattr(pdf_text,'read_pdf',lambda p:result())
    path.write_bytes(b'changed')
    with pytest.raises(ValueError,match='bytes'):pdf_text.source_text(conn,row,recover=True)
    assert conn.execute('SELECT COUNT(*) FROM pdf_text').fetchone()[0]==0
    path.write_bytes(b'%PDF-test');pdf_text.source_text(conn,row,recover=True)
    conn.execute("UPDATE pdf_text SET text='edited OCR text'")
    with pytest.raises(ValueError,match='derived text'):pdf_text.source_text(conn,row)


def test_accepted_text_is_portable_but_a_new_missing_original_stays_textless(conn,tmp_path,monkeypatch):
    row,path=held(conn,tmp_path);monkeypatch.setattr(pdf_text,'read_pdf',lambda p:result())
    pdf_text.source_text(conn,row,recover=True);path.unlink()
    assert pdf_text.source_text(conn,row)=='Supplier Alpha Ltd. $1,234.50'
    conn.execute('DELETE FROM pdf_text')
    assert pdf_text.source_text(conn,row,recover=True) is None


def test_failed_ocr_is_a_visible_document_failure_and_sends_no_model_request(conn,tmp_path,monkeypatch):
    held(conn,tmp_path)
    def fail(_):raise ValueError('Tesseract unavailable')
    monkeypatch.setattr(pdf_text,'read_pdf',fail)
    class Client:
        def extract(self,text):pytest.fail('failed OCR was sent to the model')
    stats=extract_corpus(conn,'bd_report',client=Client(),labels={})
    assert stats['errors']==1 and stats['extracted']==0
    assert conn.execute('SELECT COUNT(*) FROM pdf_text').fetchone()[0]==0


def test_renderer_or_page_failure_cannot_store_partial_document(conn,tmp_path,monkeypatch):
    row,_=held(conn,tmp_path)
    def fail(_):raise ValueError('OCR page 2 failed')
    monkeypatch.setattr(pdf_text,'read_pdf',fail)
    with pytest.raises(ValueError,match='page 2'):pdf_text.source_text(conn,row,recover=True)
    assert conn.execute('SELECT COUNT(*) FROM pdf_text').fetchone()[0]==0


def write_pdf(path,objects):
    data=b'%PDF-1.4\n';offsets=[]
    for i,obj in enumerate(objects,1):
        offsets.append(len(data));data+=f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n'
    xref=len(data);data+=f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode()
    data+=b''.join(f'{offset:010} 00000 n \n'.encode() for offset in offsets)
    data+=f'trailer << /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()
    path.write_bytes(data)


def make_scan(tmp_path):
    import subprocess,zlib
    stream=b'BT /F1 16 Tf 1 0 0 1 30 110 Tm (Bidder                  Bid Price including HST) Tj ET\nBT /F1 16 Tf 1 0 0 1 30 80 Tm (Alpha Ltd.             $1,234.50) Tj ET\nBT /F1 16 Tf 1 0 0 1 30 50 Tm (Beta Inc.               $2,345.60) Tj ET'
    native=tmp_path/'native.pdf'
    write_pdf(native,[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 450 150] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream'])
    subprocess.run(['pdftoppm','-r','300','-singlefile',str(native),str(tmp_path/'image')],check=True,capture_output=True)
    magic,width,height,maximum,pixels=(tmp_path/'image.ppm').read_bytes().split(maxsplit=4)
    assert magic==b'P6' and maximum==b'255' and len(pixels)==int(width)*int(height)*3
    image=zlib.compress(pixels);paint=b'q 450 0 0 150 0 0 cm /Im1 Do Q';scan=tmp_path/'image-only.pdf'
    write_pdf(scan,[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 450 150] /Resources << /XObject << /Im1 4 0 R >> >> /Contents 5 0 R >>',b'<< /Type /XObject /Subtype /Image /Width '+width+b' /Height '+height+b' /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length '+str(len(image)).encode()+b' >>\nstream\n'+image+b'\nendstream',b'<< /Length '+str(len(paint)).encode()+b' >>\nstream\n'+paint+b'\nendstream'])
    return native,scan


def test_real_image_pdf_recovers_both_table_rows_without_altering_original(tmp_path):
    import shutil,subprocess
    if not all(shutil.which(tool) for tool in ('tesseract','pdftoppm','pdftotext')):
        pytest.skip('Poppler and Tesseract required for the real scanned-PDF regression')
    native,scan=make_scan(tmp_path);original=scan.read_bytes()
    assert not subprocess.check_output(['pdftotext',str(scan),'-']).strip()
    found=pdf_text.read_pdf(scan)
    assert found['method']=='tesseract' and scan.read_bytes()==original
    assert 'Alpha Ltd.' in found['text'] and '$1,234.50' in found['text']
    assert 'Beta Inc.' in found['text'] and '$2,345.60' in found['text']
    page=found['provenance']['pages'][0]
    assert page['page']==1 and page['words'] and found['provenance']['language_sha256']
    assert pdf_text.read_pdf(native)['method']=='pdftotext'


def test_ocr_corrections_require_exact_raw_reading_and_publisher_corroboration():
    from toronto_bids.pdf_witness import corroborated_quotes
    proof=dict(ocr_corrections={'name':dict(before='Apha Ltd.',after='Alpha Ltd.',reason='Original scan checked; official transcription corroborates.')})
    assert corroborated_quotes(proof,{'name':'Apha Ltd.'},'Alpha Ltd.')['name']=='Alpha Ltd.'
    with pytest.raises(ValueError,match='corroboration'):corroborated_quotes(proof,{'name':'Apha Ltd.'},'A different firm')
    with pytest.raises(ValueError,match='original reading'):corroborated_quotes(proof,{'name':'changed OCR'},'Alpha Ltd.')


def test_ocr_without_source_review_cannot_derive_city_bid_facts(conn,tmp_path,monkeypatch):
    from toronto_bids.models import CouncilItem
    from toronto_bids.store.db import mark_extracted
    from toronto_bids.extract import EXTRACTOR_VERSION
    from toronto_bids.extraction import backfill_from_extraction
    row,_=held(conn,tmp_path);monkeypatch.setattr(pdf_text,'read_pdf',lambda p:result())
    pdf_text.source_text(conn,row,recover=True)
    db.upsert_row(conn,CouncilItem('2013.BD1.1',title='Award of RFQ 1234-13-1234'),overwrite=True)
    cache=json.dumps(dict(contracts=[dict(reference='1234-13-1234',bids=[dict(supplier_name='Supplier Alpha Ltd.',amount_raw='$1,234.50')])]))
    mark_extracted(conn,row['sha256'],EXTRACTOR_VERSION,cache)
    report=backfill_from_extraction(conn,'bd_report')
    assert report['contracts_refused']==1 and conn.execute('SELECT COUNT(*) FROM bid').fetchone()[0]==0
    assert conn.execute('SELECT result_json FROM extraction_cache').fetchone()[0]==cache


def test_page_bound_fails_before_ocr_engine_or_rendering(tmp_path,monkeypatch):
    calls=[]
    def run(args,**kwargs):
        calls.append(args[0]);return '' if args[0]=='pdftotext' else 'Pages: 41\n'
    monkeypatch.setattr(pdf_text,'_run',run)
    with pytest.raises(ValueError,match='40-page'):pdf_text.read_pdf(tmp_path/'large.pdf')
    assert calls==['pdftotext','pdfinfo']


@pytest.mark.parametrize('kind,before,after', [('name','Alpha Ltd.','Beta Inc.'),('price','$1,234.50','$1,243.50')])
def test_ocr_cleanup_cannot_swap_firms_or_repair_digits_even_if_both_appear_in_city_text(kind,before,after):
    from toronto_bids.pdf_witness import corroborated_quotes
    proof=dict(participants=[dict(name_witness='name')],responses=[dict(prices=[dict(value_witness='price',header_witness='header')])],ocr_corrections={kind:dict(before=before,after=after,reason='Claimed recognition cleanup')})
    quotes=dict(name='Alpha Ltd.',price='$1,234.50',header='Price')
    with pytest.raises(ValueError,match='supplier identity or price digits'):
        corroborated_quotes(proof,quotes,'Alpha Ltd. Beta Inc. $1,234.50 $1,243.50')


def test_entire_image_cohort_reconciles_source_cells_and_preserves_qualifications():
    from toronto_bids import pdf_witness
    from toronto_bids.linking.city_scoped_bids import projection
    from toronto_bids.linking.city_crosswalk import digest
    from toronto_bids.extract import EXTRACTOR_VERSION
    import gzip
    data=Path(__file__).resolve().parents[1]/'toronto_bids/data'
    reviews=[p for p in json.loads((data/'city_scoped_bid_reviews.json').read_text())['reviews'] if p.get('image_cohort')=='2026-11-06']
    records={r['sha256']:r for r in json.loads(gzip.decompress((data/'city_image_pdf_text.json.gz').read_bytes()))}
    assert len(reviews)==len(records)==11
    public=json.loads((Path(__file__).resolve().parents[2]/'docs/research/city-image-pdf-review-2026-11-06.json').read_text())
    by_doc={case['document_number']:case for case in public['cases']}
    assert set(by_doc)=={p['document_number'] for p in reviews}
    assert all(digest(p)==by_doc[p['document_number']]['scoped_review_sha256'] for p in reviews)
    assert sum(len(p['scopes']) for p in reviews)==17
    assert sum(len(p['responses']) for p in reviews)==79
    assert sum(len(r['prices']) for p in reviews for r in p['responses'])==66
    assert sum(len(p['participants']) for p in reviews)==64
    for p in reviews:
        record=records[p['report_sha256']]
        assert record['proof_sha256']==p['ocr_text_proof_sha256'] and record['text_sha256']==p['text_sha256']
        assert hashlib.sha256(record['text'].encode()).hexdigest()==record['text_sha256']
        provenance=json.loads(record['provenance_json'])
        raw=pdf_witness.ocr_quotes(p['witnesses'],provenance)
        quotes=pdf_witness.corroborated_quotes(p,raw,p['corroborating_transcription'])
        bids=projection(p,quotes,p['document_number'])
        assert len(bids)==len(p['participants']) and all(b.bid_price is None for b in bids)
        assert p['extractor_version']==EXTRACTOR_VERSION
    schedules=next(p for p in reviews if p['document_number']=='3714130002')
    assert len(schedules['participants'])==4 and len(schedules['scopes'])==7 and len(schedules['responses'])==19
    partial=next(p for p in reviews if p['document_number']=='0208130047')
    assert len(partial['responses'])==2 and all(not r['prices'] for r in partial['responses'])
    assert {r['name'] for r in partial['responses']}=={'Goodbye Graffiti','Sparkleen Mobile Wash Inc.'}
    qualified=next(p for p in reviews if p['document_number']=='1212160057')
    for r in qualified['responses']:
        for price in r['prices']:
            basis=' '.join(pdf_witness.reviewed_quote(qualified,k) for k in price['basis_witnesses'])
            assert '25%' in basis
            assert ('3.5%' in basis)==(r['name']=='Tacel Ltd')
            assert ('4%' in basis)==(r['name']=='Innovative Traffic Solutions')
    alltax=next(p for p in reviews if p['document_number']=='3717137042')
    assert all(v['hst_basis'] is None for r in alltax['responses'] for v in r['prices'])
    assert 'incl. all applicable taxes and charges' in pdf_witness.reviewed_quote(alltax,'price-header')
    literal=next(p for p in reviews if p['document_number']=='9144137033')
    assert 'Yvelo Mailng Services Inc.' in {r['name'] for r in literal['responses']}
    assert 'Yvelo Mailing Services Inc.' not in {r['name'] for r in literal['responses']}


def test_shipped_review_lookup_is_read_only_until_original_bytes_are_accepted(conn,tmp_path,monkeypatch):
    row,path=held(conn,tmp_path);monkeypatch.setattr(pdf_text,'read_pdf',lambda p:result())
    pdf_text.source_text(conn,row,recover=True)
    record=dict(conn.execute('SELECT * FROM pdf_text').fetchone())
    conn.execute('DELETE FROM pdf_text');conn.commit()
    monkeypatch.setattr(pdf_text,'_bundled',lambda:{row['sha256']:record})
    assert pdf_text.source_text(conn,row)=='Supplier Alpha Ltd. $1,234.50'
    assert conn.execute('SELECT COUNT(*) FROM pdf_text').fetchone()[0]==0
    path.write_bytes(b'changed original')
    with pytest.raises(ValueError,match='bytes changed'):pdf_text.source_text(conn,row,recover=True)
    assert conn.execute('SELECT COUNT(*) FROM pdf_text').fetchone()[0]==0
    path.write_bytes(b'%PDF-test');pdf_text.source_text(conn,row,recover=True)
    first=conn.execute('SELECT first_seen FROM pdf_text').fetchone()[0]
    pdf_text.source_text(conn,row,recover=True)
    assert conn.execute('SELECT first_seen FROM pdf_text').fetchone()[0]==first


def test_missing_ocr_output_is_a_document_error_without_partial_acceptance(conn,tmp_path,monkeypatch):
    row,_=held(conn,tmp_path);(tmp_path/'eng.traineddata').write_bytes(b'language-model')
    def run(args,**kwargs):
        if args[0]=='pdfinfo':return 'Pages: 1\n'
        if args[0]=='tesseract' and args[1]=='--version':return 'tesseract test\n'
        if args[0]=='tesseract' and args[1]=='--list-langs':return f'List of available languages in "{tmp_path}" (1):\neng\n'
        return ''
    monkeypatch.setattr(pdf_text,'_run',run)
    with pytest.raises(ValueError,match='complete text/word output'):pdf_text.source_text(conn,row,recover=True)
    assert conn.execute('SELECT COUNT(*) FROM pdf_text').fetchone()[0]==0
    assert conn.execute('SELECT text FROM background_pdf').fetchone()[0] is None
