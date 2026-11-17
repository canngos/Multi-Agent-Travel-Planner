"""Shared data models. Tool wrappers return these so agents can compare sources like for like."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

BudgetTier = Literal["low", "mid", "high"]
PriceSource = Literal["live", "web_estimate"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TripBrief(_Model):
    origin: str = "Helsinki"
    destination: str = Field(min_length=1)
    start_date: date  # ISO string in and out, e.g. "2027-05-10"
    end_date: date
    travellers: int = Field(default=2, ge=1, le=20)
    budget_tier: BudgetTier = "mid"
    total_budget_eur: float | None = Field(default=None, gt=0)
    interests: list[str] = Field(default_factory=list)  # "history", "street food", ...
    # Filled by the concierge from geocode_place, so downstream tools need no extra lookups.
    origin_lat: float | None = Field(default=None, ge=-90, le=90)
    origin_lon: float | None = Field(default=None, ge=-180, le=180)
    destination_lat: float | None = Field(default=None, ge=-90, le=90)
    destination_lon: float | None = Field(default=None, ge=-180, le=180)
    destination_country: str | None = None

    @model_validator(mode="after")
    def _check_dates(self) -> "TripBrief":
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        return self

    @property
    def nights(self) -> int:
        return (self.end_date - self.start_date).days

    @property
    def days(self) -> int:
        return self.nights + 1


class TransportOption(_Model):
    mode: Literal["flight", "train", "bus", "ferry"]
    operator: str
    depart: str  # ISO datetime, local time
    arrive: str
    duration_min: int = Field(ge=0)
    stops: int = Field(default=0, ge=0)
    price_eur: float | None = Field(default=None, ge=0)
    price_source: PriceSource | None = None  # required when price_eur is set
    source: str  # "kiwi", "transitous+tavily", ...
    url: str
    details: str = ""  # e.g. leg summary "tram 4 → ferry (Tallink) → bus 35"

    @model_validator(mode="after")
    def _price_needs_source(self) -> "TransportOption":
        if self.price_eur is not None and self.price_source is None:
            raise ValueError("price_source is required when price_eur is set")
        return self


class StayOption(_Model):
    kind: Literal["airbnb", "hotel"]
    name: str
    area: str  # neighbourhood
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    price_per_night_eur: float = Field(ge=0)
    total_price_eur: float = Field(ge=0)
    rating: float | None = Field(default=None, ge=0)  # source's own scale; say which in `details`
    price_source: PriceSource
    source: str
    url: str
    details: str = ""  # e.g. "4★, breakfast included, excl. city tax €24"


class Place(_Model):  # sights and restaurants
    kind: Literal["sight", "restaurant", "cafe", "bar"]
    name: str
    lat: float | None = Field(default=None, ge=-90, le=90)  # some sources (Wikivoyage, web) lack coordinates
    lon: float | None = Field(default=None, ge=-180, le=180)
    summary: str = ""  # short history, or cuisine and dishes
    budget_tier: BudgetTier | None = None
    hours: str | None = None
    ticket_eur: float | None = Field(default=None, ge=0)
    indoor: bool | None = None
    source_urls: list[str] = Field(default_factory=list)
