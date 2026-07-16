from typing import Iterable, Protocol, runtime_checkable

from toronto_bids.models import (AribaPosting, Award, CapitalProject, NonCompetitive,
                                  Solicitation, SuspendedFirm)

Row = Solicitation | Award | NonCompetitive | AribaPosting | SuspendedFirm | CapitalProject


@runtime_checkable
class Source(Protocol):
    name: str
    overwrite: bool

    def fetch(self, http) -> Iterable[dict]:
        ...

    def normalize(self, raw: dict) -> Iterable[Row]:
        ...
