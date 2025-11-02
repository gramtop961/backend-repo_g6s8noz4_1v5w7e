from datetime import datetime
from typing import Optional, Literal, List
from pydantic import BaseModel, Field, EmailStr

# Contact collection
class Contact(BaseModel):
    name: str = Field(..., description="Full name")
    phone: str = Field(..., description="E.164 phone number, e.g., +15551234567")
    email: Optional[EmailStr] = Field(None, description="Email address")
    headshot_url: Optional[str] = Field(None, description="URL to uploaded headshot")
    city: Optional[str] = Field(None)
    state: Optional[str] = Field(None)
    segment: Literal["local", "out_of_state"] = Field("local", description="Contact notification segment")
    brand_queue: Literal["anomaly", "arcane"] = Field("anomaly", description="Approval queue")
    status: Literal["pending", "approved", "rejected"] = Field("pending")
    phone_verified: bool = Field(False)
    referral_code: Optional[str] = Field(None, description="Unique code to invite others")
    referred_by: Optional[str] = Field(None, description="Referral code of inviter")
    # Internal transient fields (not for frontend display)
    verification_code: Optional[str] = Field(None, description="Temporary 6-digit SMS code for phone verification")


# Event collection
class Event(BaseModel):
    name: str
    date: datetime
    type: Literal["club", "after", "special", "private"] = "club"
    flyer_url: Optional[str] = None
    gate_code: Optional[str] = Field(None, description="Door gate code sent day-of")
    ticket_price: float = Field(30.0, ge=0)
    status: Literal["draft", "scheduled", "completed", "cancelled"] = "scheduled"


# RSVP collection
class Rsvp(BaseModel):
    contact_id: str = Field(..., description="ID of contact")
    event_id: str = Field(..., description="ID of event")
    status: Literal["yes", "not_this_time", "no_response"] = "no_response"
    qr_code_token: Optional[str] = Field(None, description="Unique token used for QR check-in")
    sent_gate_code: bool = False


# SMS message log collection
class Smsmessage(BaseModel):  # collection name: smsmessage
    to: str
    body: str
    purpose: Literal["verify", "invite", "reminder", "gate_code"]
    status: Literal["queued", "sent", "delivered", "failed"] = "queued"
    provider_message_sid: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    logs: Optional[List[dict]] = Field(default_factory=list)
