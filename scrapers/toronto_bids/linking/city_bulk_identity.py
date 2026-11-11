"""Independent original-award identity for historical bulk report participation."""
from collections import defaultdict
import json

from toronto_bids.linking import city_archived_awards as archived
from toronto_bids.linking import city_crosswalk as cw


class NativeCallIdentities:
    """Load the complete comparison population once, only if a native call needs it.

    Model references are locators. A join requires the original printed award
    date, full supplier and exact amount to identify one frozen and current City
    record, with no competing call in any retained archive view.
    """
    def __init__(self, conn):
        self.conn=conn
        self.evidence=None

    def resolve(self, call):
        if self.evidence is None:
            self.evidence=archived.load_evidence(json.loads(cw.PROOFS.read_text()),cw.PROOFS)
            self.current=archived.current_index(self.conn)
            self.by_call=defaultdict(list)
            for (sha,ordinal),row in self.evidence.rows.items():
                self.by_call[row['call_number']].append((sha,ordinal))
        docs=self.evidence.call_documents.get(call,set())
        if len(docs)!=1:raise ValueError('native_call_has_no_unique_original_award_identity')
        doc=next(iter(docs))
        for sha,ordinal in sorted(self.by_call[call]):
            try:
                award,publisher,_=archived.validate_current_award(
                    self.conn,self.evidence,doc,sha,ordinal,self.current)
            except ValueError:
                continue
            return doc,dict(call_number=call,snapshot_sha256=self.evidence.snapshot_sha256,
                publisher_record_sha256=cw.digest(publisher),archived_award=dict(
                    source_sha256=sha,row_ordinal=ordinal,row_sha256=award['row_sha256'],
                    source_url=award['source']['url'],printed_award_date=award['published_award_date']))
        raise ValueError('native_call_current_award_identity_changed_or_unproved')
