from collections.abc import Iterator

from reacher.sources.base import RawSalon


class ScbApiSource:
    """SCB:s företagsregister. Väntar på beviljad tillgång; nytt nyckel-API sep 2026.
    Bransch, adress och antal anställda är avgiftsfria. Telefon är det inte.
    Max 2000 rader per anrop -> paginera.
    """

    name = "scb"

    def fetch(self) -> Iterator[RawSalon]:
        raise NotImplementedError("Blockerad på API-tillgång - använd csv-källan")
