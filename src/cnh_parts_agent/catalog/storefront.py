"""Verified US storefront identities; never accept arbitrary caller URLs."""

from dataclasses import dataclass

from .errors import PartsError

ORIGIN = "https://www.mycnhstore.com"


@dataclass(frozen=True)
class Storefront:
    brand: str
    label: str
    market: str = "us"
    language: str = "en"
    region_code: str = "NA"

    @property
    def source_id(self) -> str:
        return f"{self.brand}-{self.market}-{self.language}"

    @property
    def home_path(self) -> str:
        return f"/{self.market}/{self.language}/{self.brand}"

    @property
    def home(self) -> str:
        return ORIGIN + self.home_path


CASECE = Storefront("casece", "CASE Construction")
CASEIH = Storefront("caseih", "Case IH")
NEWHOLLANDAG = Storefront("newhollandag", "New Holland Agriculture")
NEWHOLLANDCE = Storefront("newhollandce", "New Holland Construction")
STOREFRONTS = {s.brand: s for s in (CASECE, CASEIH, NEWHOLLANDAG, NEWHOLLANDCE)}


def get_storefront(brand: str) -> Storefront:
    if brand not in STOREFRONTS:
        raise PartsError(
            "unsupported_source", "Choose a registered storefront: " + ", ".join(STOREFRONTS) + "."
        )
    return STOREFRONTS[brand]
