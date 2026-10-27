from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Listing:
    platform: str          # "wallapop" | "vinted"
    item_id: str
    title: str
    price: float
    url: str
    description: str = ""
    image: Optional[str] = None
    location: str = ""
    shipping: bool = False
    reserved: bool = False
    created_ts: Optional[float] = None   # epoch seconds
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.item_id}"
