from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import DownloadClient
from .models import Candidate, DownloadReceipt, TransferKind


@dataclass(slots=True)
class Route:
    name: str
    client: DownloadClient
    destination: str
    transfer_kinds: set[TransferKind] = field(default_factory=set)
    require_tags: set[str] = field(default_factory=set)
    exclude_tags: set[str] = field(default_factory=set)

    def accepts(self, candidate: Candidate) -> bool:
        if self.transfer_kinds and candidate.transfer_kind not in self.transfer_kinds:
            return False
        if self.require_tags - candidate.tags:
            return False
        if self.exclude_tags & candidate.tags:
            return False
        return True

    def submit(self, candidate: Candidate) -> DownloadReceipt:
        return self.client.submit(candidate, self.destination)
