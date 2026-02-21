from typing import Optional, List
from datetime import datetime
from sqlmodel import Field, SQLModel, Relationship


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    hashed_password: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    businesses: List["Business"] = Relationship(back_populates="owner")


class Business(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    slug: str = Field(unique=True, index=True)
    logo_url: Optional[str] = None
    tagline: Optional[str] = None
    accent_color: str = Field(default="#6366f1")
    thankyou_message: Optional[str] = None
    scan_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    owner_id: Optional[int] = Field(default=None, foreign_key="user.id")
    owner: Optional[User] = Relationship(back_populates="businesses")
    review_links: List["ReviewLink"] = Relationship(back_populates="business")
    scan_events: List["ScanEvent"] = Relationship(back_populates="business")
    feedback_items: List["FeedbackItem"] = Relationship(back_populates="business")
    cards: List["Card"] = Relationship(back_populates="business")


class ReviewLink(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    platform_name: str
    url: str
    icon: str = Field(default="default")
    clicks: int = Field(default=0)
    sort_order: int = Field(default=0)
    is_active: bool = Field(default=True)
    business_id: Optional[int] = Field(default=None, foreign_key="business.id")
    business: Optional[Business] = Relationship(back_populates="review_links")


class ScanEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    scanned_at: datetime = Field(default_factory=datetime.utcnow)
    business_id: Optional[int] = Field(default=None, foreign_key="business.id")
    business: Optional[Business] = Relationship(back_populates="scan_events")


class FeedbackItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    message: str
    submitted_at: datetime = Field(default_factory=datetime.utcnow)
    is_read: bool = Field(default=False)
    business_id: Optional[int] = Field(default=None, foreign_key="business.id")
    business: Optional[Business] = Relationship(back_populates="feedback_items")


class CardLink(SQLModel, table=True):
    """Social/web links on a Card (LinkedIn, website, Instagram, etc.)"""
    id: Optional[int] = Field(default=None, primary_key=True)
    label: str
    url: str
    icon: str = Field(default="link")
    clicks: int = Field(default=0)
    sort_order: int = Field(default=0)
    card_id: Optional[int] = Field(default=None, foreign_key="card.id")
    card: Optional["Card"] = Relationship(back_populates="links")


class Card(SQLModel, table=True):
    """Digital business card – one per employee."""
    id: Optional[int] = Field(default=None, primary_key=True)
    slug: str = Field(index=True)
    full_name: str
    title: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    photo_path: Optional[str] = None
    bio: Optional[str] = None
    accent_color: Optional[str] = None
    vcard_enabled: bool = Field(default=True)
    views: int = Field(default=0)
    vcard_downloads: int = Field(default=0)
    wallet_installs: int = Field(default=0)
    wallet_opens: int = Field(default=0)
    clicks_phone: int = Field(default=0)
    clicks_email: int = Field(default=0)
    clicks_whatsapp: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    business_id: Optional[int] = Field(default=None, foreign_key="business.id")
    business: Optional[Business] = Relationship(back_populates="cards")
    links: List[CardLink] = Relationship(back_populates="card")
