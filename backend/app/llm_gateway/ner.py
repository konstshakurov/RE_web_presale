"""NER на natasha (ФИО, организации, локации) за портом ``NerPort``. Модели грузятся лениво."""
from __future__ import annotations

import logging
import threading

from .anonymize import COMPANY, LOCATION, PERSON, NerSpan

log = logging.getLogger(__name__)

_KINDS = {"PER": PERSON, "ORG": COMPANY, "LOC": LOCATION}


class NatashaNer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._segmenter = None
        self._tagger = None

    @staticmethod
    def available() -> bool:
        try:
            import natasha  # noqa: F401
        except ImportError:
            return False
        return True

    def _load(self) -> None:
        with self._lock:
            if self._tagger is None:
                from natasha import NewsEmbedding, NewsNERTagger, Segmenter

                self._segmenter = Segmenter()
                self._tagger = NewsNERTagger(NewsEmbedding())

    def extract(self, text: str) -> list[NerSpan]:
        from natasha import Doc

        self._load()
        doc = Doc(text)
        doc.segment(self._segmenter)
        doc.tag_ner(self._tagger)
        return [
            NerSpan(_KINDS[s.type], s.start, s.stop) for s in doc.spans if s.type in _KINDS
        ]


def build_ner() -> NatashaNer | None:
    """NER, если пакет установлен; иначе None (работают regex и словари, об этом пишется в лог)."""
    if NatashaNer.available():
        return NatashaNer()
    log.warning("natasha не установлена: NER-этап обезличивания отключён")
    return None
