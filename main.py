import os
import random
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr

from database import db, create_document, get_documents
from schemas import Contact, Event, Rsvp, Smsmessage

# Optional Twilio; only used if credentials exist
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER")

twilio_client = None
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    try:
        from twilio.rest import Client  # type: ignore
        twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    except Exception:
        twilio_client = None

app = FastAPI(title="Anomaly Events Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Utility helpers

def _collection(name: str):
    if db is None:
        raise HTTPException(500, "Database not available")
    return db[name]


def _normalize_phone(phone: str) -> str:
    # Basic normalization to keep digits and ensure + prefix if provided
    digits = "".join(ch for ch in phone if ch.isdigit())
    if phone.strip().startswith("+"):
        return "+" + digits
    # Default to US if 10 digits
    if len(digits) == 10:
        return "+1" + digits
    return "+" + digits if not digits.startswith("+") else digits


def _send_sms(to: str, body: str, purpose: str) -> Optional[str]:
    """Send SMS via Twilio if configured. Returns provider message sid or None."""
    sid = None
    try:
        to_norm = _normalize_phone(to)
        if twilio_client and TWILIO_FROM_NUMBER:
            msg = twilio_client.messages.create(
                to=to_norm,
                from_=TWILIO_FROM_NUMBER,
                body=body,
                status_callback=os.getenv("TWILIO_STATUS_WEBHOOK") or None,
            )
            sid = msg.sid
            create_document("smsmessage", Smsmessage(to=to_norm, body=body, purpose=purpose, status="sent", provider_message_sid=sid))
        else:
            # Fallback: log only
            create_document("smsmessage", Smsmessage(to=to_norm, body=body, purpose=purpose, status="queued"))
    except Exception as e:
        create_document("smsmessage", Smsmessage(to=to_norm, body=body, purpose=purpose, status="failed", error_message=str(e)))
    return sid


# Models for requests
class RegistrationRequest(BaseModel):
    name: str
    phone: str
    email: Optional[EmailStr] = None
    headshot_url: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    brand_queue: Optional[str] = "anomaly"
    referred_by: Optional[str] = None


class VerifySendRequest(BaseModel):
    phone: str


class VerifyConfirmRequest(BaseModel):
    phone: str
    code: str


class EventCreateRequest(BaseModel):
    name: str
    date: datetime
    type: Optional[str] = "club"
    flyer_url: Optional[str] = None
    gate_code: Optional[str] = None
    ticket_price: Optional[float] = 30.0


class RsvpRequest(BaseModel):
    contact_id: str
    event_id: str
    status: str  # yes | not_this_time | no_response


# Routes
@app.get("/")
def root():
    return {"message": "Anomaly Events Backend running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set",
        "database_name": "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set",
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["connection_status"] = "Connected"
            response["collections"] = db.list_collection_names()[:10]
        else:
            response["database"] = "❌ Not Available"
    except Exception as e:
        response["database"] = f"⚠️ Error: {str(e)[:80]}"
    return response


# Contact registration and verification
@app.post("/contacts/register")
def register_contact(payload: RegistrationRequest):
    phone = _normalize_phone(payload.phone)
    # Check if exists
    existing = _collection("contact").find_one({"phone": phone})
    if existing:
        # Allow re-send verification if not verified
        code = str(random.randint(100000, 999999))
        _collection("contact").update_one({"_id": existing["_id"]}, {"$set": {"verification_code": code, "updated_at": datetime.now(timezone.utc)}})
        _send_sms(phone, f"Anomaly verification code: {code}", purpose="verify")
        return {"id": str(existing["_id"]), "phone": phone, "message": "Contact exists, verification re-sent"}

    # Create new contact
    code = str(random.randint(100000, 999999))
    segment = "local" if (payload.state or "").strip().upper() in {"CA", "NY", "NV", "WA", "OR", "TX", "FL"} else "out_of_state"
    contact = Contact(
        name=payload.name,
        phone=phone,
        email=payload.email,
        headshot_url=payload.headshot_url,
        city=payload.city,
        state=payload.state,
        brand_queue=(payload.brand_queue or "anomaly").lower(),
        segment=segment,
        status="pending",
        phone_verified=False,
        referred_by=payload.referred_by,
        verification_code=code,
    )
    contact_id = create_document("contact", contact)
    _send_sms(phone, f"Anomaly verification code: {code}", purpose="verify")
    return {"id": contact_id, "phone": phone, "message": "Registration received. Verification sent."}


@app.post("/contacts/verify/send")
def send_verification(payload: VerifySendRequest):
    phone = _normalize_phone(payload.phone)
    contact = _collection("contact").find_one({"phone": phone})
    if not contact:
        raise HTTPException(404, "Contact not found")
    code = str(random.randint(100000, 999999))
    _collection("contact").update_one({"_id": contact["_id"]}, {"$set": {"verification_code": code, "updated_at": datetime.now(timezone.utc)}})
    _send_sms(phone, f"Anomaly verification code: {code}", purpose="verify")
    return {"message": "Verification sent"}


@app.post("/contacts/verify/confirm")
def confirm_verification(payload: VerifyConfirmRequest):
    phone = _normalize_phone(payload.phone)
    contact = _collection("contact").find_one({"phone": phone})
    if not contact:
        raise HTTPException(404, "Contact not found")
    if payload.code != contact.get("verification_code"):
        raise HTTPException(400, "Invalid verification code")

    _collection("contact").update_one(
        {"_id": contact["_id"]},
        {"$set": {"phone_verified": True, "verification_code": None, "updated_at": datetime.now(timezone.utc)}}
    )
    return {"message": "Phone verified", "contact_id": str(contact["_id"])}


@app.get("/contacts")
def list_contacts(status: Optional[str] = None, brand: Optional[str] = None):
    filt = {}
    if status:
        filt["status"] = status
    if brand:
        filt["brand_queue"] = brand
    docs = get_documents("contact", filt)
    for d in docs:
        d["_id"] = str(d["_id"])
        d.pop("verification_code", None)
    return docs


@app.post("/events")
def create_event(payload: EventCreateRequest):
    event = Event(
        name=payload.name,
        date=payload.date,
        type=payload.type or "club",
        flyer_url=payload.flyer_url,
        gate_code=payload.gate_code,
        ticket_price=payload.ticket_price or 30.0,
        status="scheduled",
    )
    event_id = create_document("event", event)
    return {"id": event_id}


@app.get("/events")
def list_events():
    docs = get_documents("event")
    for d in docs:
        d["_id"] = str(d["_id"])
    return docs


@app.post("/rsvps")
def upsert_rsvp(payload: RsvpRequest):
    from bson import ObjectId  # type: ignore
    try:
        contact_id = ObjectId(payload.contact_id)
        event_id = ObjectId(payload.event_id)
    except Exception:
        raise HTTPException(400, "Invalid IDs")

    if not _collection("contact").find_one({"_id": contact_id}):
        raise HTTPException(404, "Contact not found")
    if not _collection("event").find_one({"_id": event_id}):
        raise HTTPException(404, "Event not found")

    r = _collection("rsvp").find_one({"contact_id": payload.contact_id, "event_id": payload.event_id})
    if r:
        _collection("rsvp").update_one({"_id": r["_id"]}, {"$set": {"status": payload.status, "updated_at": datetime.now(timezone.utc)}})
        rsvp_id = str(r["_id"])
    else:
        rsvp = Rsvp(contact_id=payload.contact_id, event_id=payload.event_id, status=payload.status)
        rsvp_id = create_document("rsvp", rsvp)
    return {"id": rsvp_id, "status": payload.status}


@app.post("/sms/webhook")
async def sms_status_webhook(request: Request):
    # Twilio status callback webhook
    form = await request.form()
    message_sid = form.get("MessageSid")
    message_status = form.get("MessageStatus")
    to = form.get("To")
    error_code = form.get("ErrorCode")

    if not message_sid:
        return {"ok": True}

    _collection("smsmessage").update_one(
        {"provider_message_sid": message_sid},
        {"$set": {"status": message_status, "updated_at": datetime.now(timezone.utc)},
         "$push": {"logs": {"status": message_status, "at": datetime.now(timezone.utc), "to": to, "error_code": error_code}}},
        upsert=True,
    )
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
