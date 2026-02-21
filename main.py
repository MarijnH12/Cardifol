import io, base64, json, os, uuid
from typing import Optional
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, SQLModel, create_engine, select

from models import Business, ReviewLink, ScanEvent, FeedbackItem, User, Card, CardLink
from auth import hash_password, verify_password, create_session_token, get_current_user, require_user

DATABASE_URL = "sqlite:///./cardifol.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
app = FastAPI(title="Cardifol")

def _sqlite_add_column_if_missing(table: str, column: str, coltype: str, default_sql: str = "0"):
    """Best-effort SQLite migration: adds a column if it doesn't exist."""
    db_path = engine.url.database or "cardifol.db"
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table});")
        cols = {row[1] for row in cur.fetchall()}
        if column not in cols:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype} NOT NULL DEFAULT {default_sql};")
            conn.commit()
        conn.close()
    except Exception:
        # Don't crash startup on migration issues; keep app booting.
        pass


templates = Jinja2Templates(directory="templates")

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# ── DB ────────────────────────────────────────────────────────────────────────

def get_session():
    with Session(engine) as s:
        yield s

@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(engine)
    # Lightweight schema migration for existing SQLite DBs
    _sqlite_add_column_if_missing("card", "wallet_installs", "INTEGER", "0")
    _sqlite_add_column_if_missing("card", "wallet_opens", "INTEGER", "0")

# ── Upload helpers ────────────────────────────────────────────────────────────

ALLOWED = {"image/jpeg", "image/png", "image/webp", "image/gif"}

async def save_upload(file: UploadFile) -> Optional[str]:
    if not file or not file.filename:
        return None
    if file.content_type not in ALLOWED:
        raise HTTPException(400, "Only JPEG/PNG/WebP/GIF allowed.")
    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(400, "Max 5 MB.")
    ext = file.filename.rsplit(".", 1)[-1].lower()
    fname = f"{uuid.uuid4().hex}.{ext}"
    (UPLOAD_DIR / fname).write_bytes(data)
    return f"/uploads/{fname}"

def del_upload(path: Optional[str]):
    if path and path.startswith("/uploads/"):
        p = Path(path.lstrip("/"))
        if p.exists(): p.unlink()

# ── QR helper ─────────────────────────────────────────────────────────────────

def make_qr(url: str, dark: str = "#111827") -> str:
    try:
        import segno
        qr = segno.make(url, error="M")
        buf = io.BytesIO()
        qr.save(buf, kind="svg", scale=5, border=2, dark=dark, light="#f9fafb")
        return base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        return ""

# ── Stats helpers ─────────────────────────────────────────────────────────────

def hourly_pulse(session: Session, biz_id: int):
    today = datetime.utcnow().date()
    start = datetime(today.year, today.month, today.day)
    events = session.exec(
        select(ScanEvent).where(
            ScanEvent.business_id == biz_id,
            ScanEvent.scanned_at >= start,
            ScanEvent.scanned_at < start + timedelta(days=1),
        )
    ).all()
    counts = defaultdict(int)
    for e in events:
        counts[e.scanned_at.hour] += 1
    return [counts[h] for h in range(24)]

# ── Auth guard ────────────────────────────────────────────────────────────────

def get_biz(slug: str, user: User, session: Session) -> Business:
    biz = session.exec(select(Business).where(Business.slug == slug)).first()
    if not biz or biz.owner_id != user.id:
        raise HTTPException(403, "Forbidden")
    return biz

def card_color(card: Card) -> str:
    return card.accent_color or (card.business.accent_color if card.business else "#6366f1")

# ══════════════════════════════════════════════════════════════════════════════
# AUTH
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request, session: Session = Depends(get_session)):
    if get_current_user(request, session): return RedirectResponse("/dashboard", 302)
    return templates.TemplateResponse("auth.html", {"request": request, "mode": "register", "error": None})

@app.post("/register", response_class=HTMLResponse)
def register(request: Request, email: str = Form(...), password: str = Form(...), session: Session = Depends(get_session)):
    if session.exec(select(User).where(User.email == email)).first():
        return templates.TemplateResponse("auth.html", {"request": request, "mode": "register", "error": "Email already registered."})
    if len(password) < 8:
        return templates.TemplateResponse("auth.html", {"request": request, "mode": "register", "error": "Password must be at least 8 characters."})
    user = User(email=email, hashed_password=hash_password(password))
    session.add(user); session.commit(); session.refresh(user)
    resp = RedirectResponse("/dashboard", 302)
    resp.set_cookie("cardifol_session", create_session_token(user.id), httponly=True, max_age=60*60*24*30, samesite="lax")
    return resp

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, session: Session = Depends(get_session)):
    if get_current_user(request, session): return RedirectResponse("/dashboard", 302)
    return templates.TemplateResponse("auth.html", {"request": request, "mode": "login", "error": None})

@app.post("/login", response_class=HTMLResponse)
def login(request: Request, email: str = Form(...), password: str = Form(...), session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.email == email)).first()
    if not user or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse("auth.html", {"request": request, "mode": "login", "error": "Invalid email or password."})
    resp = RedirectResponse("/dashboard", 302)
    resp.set_cookie("cardifol_session", create_session_token(user.id), httponly=True, max_age=60*60*24*30, samesite="lax")
    return resp

@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", 302)
    resp.delete_cookie("cardifol_session")
    return resp

# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)):
    user = require_user(request, session)
    businesses = session.exec(select(Business).where(Business.owner_id == user.id)).all()
    biz_stats = []
    for biz in businesses:
        cards = session.exec(select(Card).where(Card.business_id == biz.id)).all()
        biz_stats.append({
            "biz": biz,
            "card_count": len(cards),
            "total_clicks": sum(l.clicks for l in biz.review_links),
        })
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": user, "biz_stats": biz_stats})

# ══════════════════════════════════════════════════════════════════════════════
# CREATE BUSINESS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/new", response_class=HTMLResponse)
def new_biz_page(request: Request, session: Session = Depends(get_session)):
    user = require_user(request, session)
    return templates.TemplateResponse("business_form.html", {"request": request, "user": user, "biz": None, "links": [], "error": None, "mode": "create"})

@app.post("/new")
async def create_biz(
    request: Request,
    name: str = Form(...), slug: str = Form(...),
    tagline: Optional[str] = Form(None), accent_color: str = Form("#6366f1"),
    logo_file: Optional[UploadFile] = File(None),
    thankyou_message: Optional[str] = Form(None),
    google_url: Optional[str] = Form(None),
    trustpilot_url: Optional[str] = Form(None),
    tripadvisor_url: Optional[str] = Form(None),
    session: Session = Depends(get_session),
):
    user = require_user(request, session)
    slug_clean = slug.lower().strip().replace(" ", "-")
    if session.exec(select(Business).where(Business.slug == slug_clean)).first():
        return templates.TemplateResponse("business_form.html", {"request": request, "user": user, "biz": None, "links": [], "mode": "create", "error": f"Slug '{slug_clean}' already taken."})
    logo_url = await save_upload(logo_file) if logo_file and logo_file.filename else None
    biz = Business(name=name, slug=slug_clean, tagline=tagline or None, accent_color=accent_color,
                   logo_url=logo_url, thankyou_message=thankyou_message or None, owner_id=user.id)
    session.add(biz); session.commit(); session.refresh(biz)
    for pos, (platform, url, icon) in enumerate([("Google", google_url, "google"), ("Trustpilot", trustpilot_url, "trustpilot"), ("Tripadvisor", tripadvisor_url, "tripadvisor")]):
        if url:
            session.add(ReviewLink(platform_name=platform, url=url, icon=icon, sort_order=pos, business_id=biz.id))
    session.commit()
    return RedirectResponse(f"/admin/{biz.slug}", 302)

# ══════════════════════════════════════════════════════════════════════════════
# DELETE BUSINESS
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/admin/{slug}/delete")
def delete_business(slug: str, request: Request, session: Session = Depends(get_session)):
    user = require_user(request, session)
    biz = get_biz(slug, user, session)
    # Cascade delete all children
    for r in session.exec(select(ReviewLink).where(ReviewLink.business_id == biz.id)).all(): session.delete(r)
    for e in session.exec(select(ScanEvent).where(ScanEvent.business_id == biz.id)).all(): session.delete(e)
    for f in session.exec(select(FeedbackItem).where(FeedbackItem.business_id == biz.id)).all(): session.delete(f)
    for card in session.exec(select(Card).where(Card.business_id == biz.id)).all():
        for cl in session.exec(select(CardLink).where(CardLink.card_id == card.id)).all(): session.delete(cl)
        del_upload(card.photo_path)
        session.delete(card)
    del_upload(biz.logo_url)
    session.delete(biz); session.commit()
    return RedirectResponse("/dashboard", 302)

# ══════════════════════════════════════════════════════════════════════════════
# SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/settings/{slug}", response_class=HTMLResponse)
def settings_page(slug: str, request: Request, session: Session = Depends(get_session)):
    user = require_user(request, session)
    biz = get_biz(slug, user, session)
    links = session.exec(select(ReviewLink).where(ReviewLink.business_id == biz.id).order_by(ReviewLink.sort_order)).all()
    return templates.TemplateResponse("business_form.html", {"request": request, "user": user, "biz": biz, "links": links, "error": None, "mode": "edit"})

@app.post("/settings/{slug}")
async def save_settings(
    slug: str, request: Request,
    name: str = Form(...), new_slug: str = Form(...),
    tagline: Optional[str] = Form(None), accent_color: str = Form("#6366f1"),
    logo_file: Optional[UploadFile] = File(None), remove_logo: Optional[str] = Form(None),
    thankyou_message: Optional[str] = Form(None),
    session: Session = Depends(get_session),
):
    user = require_user(request, session)
    biz = get_biz(slug, user, session)
    new_slug_clean = new_slug.lower().strip().replace(" ", "-")
    if new_slug_clean != biz.slug:
        if session.exec(select(Business).where(Business.slug == new_slug_clean)).first():
            links = session.exec(select(ReviewLink).where(ReviewLink.business_id == biz.id).order_by(ReviewLink.sort_order)).all()
            return templates.TemplateResponse("business_form.html", {"request": request, "user": user, "biz": biz, "links": links, "mode": "edit", "error": f"Slug '{new_slug_clean}' already taken."})
    if logo_file and logo_file.filename:
        del_upload(biz.logo_url); biz.logo_url = await save_upload(logo_file)
    elif remove_logo == "1":
        del_upload(biz.logo_url); biz.logo_url = None
    biz.name = name; biz.slug = new_slug_clean; biz.tagline = tagline or None
    biz.accent_color = accent_color; biz.thankyou_message = thankyou_message or None
    session.add(biz); session.commit()
    return RedirectResponse(f"/admin/{new_slug_clean}", 302)

@app.post("/settings/{slug}/links/add")
async def add_link(slug: str, request: Request, platform_name: str = Form(...), url: str = Form(...), icon: str = Form("default"), session: Session = Depends(get_session)):
    user = require_user(request, session)
    biz = get_biz(slug, user, session)
    n = len(session.exec(select(ReviewLink).where(ReviewLink.business_id == biz.id)).all())
    session.add(ReviewLink(platform_name=platform_name, url=url, icon=icon, sort_order=n, business_id=biz.id))
    session.commit()
    return RedirectResponse(f"/settings/{slug}", 302)

@app.post("/settings/{slug}/links/{link_id}/delete")
def delete_link(slug: str, link_id: int, request: Request, session: Session = Depends(get_session)):
    user = require_user(request, session)
    biz = get_biz(slug, user, session)
    link = session.get(ReviewLink, link_id)
    if link and link.business_id == biz.id:
        session.delete(link); session.commit()
    return RedirectResponse(f"/settings/{slug}", 302)

@app.post("/settings/{slug}/links/{link_id}/toggle")
def toggle_link(slug: str, link_id: int, request: Request, session: Session = Depends(get_session)):
    user = require_user(request, session)
    biz = get_biz(slug, user, session)
    link = session.get(ReviewLink, link_id)
    if link and link.business_id == biz.id:
        link.is_active = not link.is_active; session.add(link); session.commit()
    return RedirectResponse(f"/admin/{slug}", 302)

# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC — Review landing
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/s/{slug}", response_class=HTMLResponse)
def landing(slug: str, request: Request, session: Session = Depends(get_session)):
    biz = session.exec(select(Business).where(Business.slug == slug)).first()
    if not biz: raise HTTPException(404)
    biz.scan_count += 1; session.add(biz)
    session.add(ScanEvent(business_id=biz.id)); session.commit(); session.refresh(biz)
    links = session.exec(select(ReviewLink).where(ReviewLink.business_id == biz.id, ReviewLink.is_active == True).order_by(ReviewLink.sort_order)).all()
    return templates.TemplateResponse("index.html", {"request": request, "biz": biz, "links": links})

@app.get("/click/{link_id}")
def click_review(link_id: int, session: Session = Depends(get_session)):
    link = session.get(ReviewLink, link_id)
    if not link: raise HTTPException(404)
    link.clicks += 1; session.add(link); session.commit()
    return RedirectResponse(link.url, 302)

@app.post("/feedback/{slug}")
async def submit_feedback(slug: str, request: Request, session: Session = Depends(get_session)):
    biz = session.exec(select(Business).where(Business.slug == slug)).first()
    if not biz: raise HTTPException(404)
    data = await request.json()
    msg = (data.get("message") or "").strip()
    if not msg: raise HTTPException(400)
    session.add(FeedbackItem(message=msg, business_id=biz.id)); session.commit()
    return JSONResponse({"ok": True})

# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC — Digital card
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/c/{biz_slug}/{card_slug}", response_class=HTMLResponse)
def card_public(biz_slug: str, card_slug: str, request: Request, session: Session = Depends(get_session)):
    biz = session.exec(select(Business).where(Business.slug == biz_slug)).first()
    if not biz: raise HTTPException(404)
    card = session.exec(select(Card).where(Card.business_id == biz.id, Card.slug == card_slug)).first()
    if not card: raise HTTPException(404)
    card.views += 1; session.add(card); session.commit(); session.refresh(card)
    card_links = session.exec(select(CardLink).where(CardLink.card_id == card.id).order_by(CardLink.sort_order)).all()
    color = card_color(card)
    base = str(request.base_url)
    card_url = f"{base}c/{biz_slug}/{card_slug}"
    card_qr = make_qr(card_url, dark="#111827")
    return templates.TemplateResponse("card_public.html", {
        "request": request, "biz": biz, "card": card,
        "card_links": card_links, "color": color,
        "card_url": card_url, "card_qr": card_qr,
    })

@app.get("/c/{biz_slug}/{card_slug}/track/{action}")
def track_card_action(biz_slug: str, card_slug: str, action: str, session: Session = Depends(get_session)):
    biz = session.exec(select(Business).where(Business.slug == biz_slug)).first()
    if not biz: raise HTTPException(404)
    card = session.exec(select(Card).where(Card.business_id == biz.id, Card.slug == card_slug)).first()
    if not card: raise HTTPException(404)
    dest = "#"
    if action == "phone":
        card.clicks_phone += 1; dest = f"tel:{card.phone}"
    elif action == "email":
        card.clicks_email += 1; dest = f"mailto:{card.email}"
    elif action == "whatsapp":
        card.clicks_whatsapp += 1
        clean = (card.phone or "").replace(" ", "").replace("-", "").replace("+", "")
        dest = f"https://wa.me/{clean}"
    elif action == "vcard":
        card.vcard_downloads += 1; session.add(card); session.commit()
        return RedirectResponse(f"/c/{biz_slug}/{card_slug}/vcard.vcf", 302)
    session.add(card); session.commit()
    return RedirectResponse(dest, 302)

@app.get("/cl/{link_id}")
def click_card_link(link_id: int, session: Session = Depends(get_session)):
    cl = session.get(CardLink, link_id)
    if not cl: raise HTTPException(404)
    cl.clicks += 1; session.add(cl); session.commit()
    return RedirectResponse(cl.url, 302)

@app.get("/c/{biz_slug}/{card_slug}/vcard.vcf")
def download_vcard(biz_slug: str, card_slug: str, session: Session = Depends(get_session)):
    biz = session.exec(select(Business).where(Business.slug == biz_slug)).first()
    card = session.exec(select(Card).where(Card.business_id == biz.id, Card.slug == card_slug)).first() if biz else None
    if not card: raise HTTPException(404)
    lines = ["BEGIN:VCARD", "VERSION:3.0", f"FN:{card.full_name}"]
    if card.title: lines.append(f"TITLE:{card.title}")
    if biz: lines.append(f"ORG:{biz.name}")
    if card.phone: lines.append(f"TEL;TYPE=CELL:{card.phone}")
    if card.email: lines.append(f"EMAIL:{card.email}")
    if card.bio: lines.append(f"NOTE:{card.bio}")
    for cl in session.exec(select(CardLink).where(CardLink.card_id == card.id)).all():
        lines.append(f"URL;TYPE={cl.label}:{cl.url}")
    lines.append("END:VCARD")
    return Response("\r\n".join(lines), media_type="text/vcard",
                    headers={"Content-Disposition": f'attachment; filename="{card.slug}.vcf"'})

# ══════════════════════════════════════════════════════════════════════════════
# ADMIN — Main dashboard
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/admin/{slug}", response_class=HTMLResponse)
def admin(slug: str, request: Request, session: Session = Depends(get_session)):
    user = require_user(request, session)
    biz = get_biz(slug, user, session)
    links = session.exec(select(ReviewLink).where(ReviewLink.business_id == biz.id).order_by(ReviewLink.sort_order)).all()
    feedback = session.exec(select(FeedbackItem).where(FeedbackItem.business_id == biz.id).order_by(FeedbackItem.submitted_at.desc())).all()
    cards = session.exec(select(Card).where(Card.business_id == biz.id)).all()
    total_clicks = sum(l.clicks for l in links)
    total_card_views = sum(c.views for c in cards)
    total_vcard_dl = sum(c.vcard_downloads for c in cards)
    pulse = hourly_pulse(session, biz.id)
    today = datetime.utcnow().date()
    start = datetime(today.year, today.month, today.day)
    scans_today = len(session.exec(select(ScanEvent).where(ScanEvent.business_id == biz.id, ScanEvent.scanned_at >= start)).all())
    base = str(request.base_url)
    landing_url = f"{base}s/{slug}"
    qr_b64 = make_qr(landing_url)
    unread = sum(1 for f in feedback if not f.is_read)
    top_cards = sorted(cards, key=lambda c: c.views, reverse=True)[:5]
    return templates.TemplateResponse("admin.html", {
        "request": request, "user": user, "biz": biz,
        "links": links, "feedback": feedback, "cards": cards, "top_cards": top_cards,
        "total_clicks": total_clicks, "total_card_views": total_card_views, "total_vcard_dl": total_vcard_dl,
        "pulse_data": json.dumps(pulse), "scans_today": scans_today,
        "landing_url": landing_url, "qr_b64": qr_b64, "unread_feedback": unread,
        "now_date": datetime.utcnow().strftime("%B %d, %Y"),
        "base_url": base,
    })

@app.post("/admin/{slug}/feedback/{fid}/read")
def mark_read(slug: str, fid: int, request: Request, session: Session = Depends(get_session)):
    user = require_user(request, session)
    item = session.get(FeedbackItem, fid)
    if item: item.is_read = True; session.add(item); session.commit()
    return RedirectResponse(f"/admin/{slug}", 302)

# ══════════════════════════════════════════════════════════════════════════════
# ADMIN — Cards overview
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/admin/{slug}/cards", response_class=HTMLResponse)
def cards_overview(slug: str, request: Request, session: Session = Depends(get_session)):
    user = require_user(request, session)
    biz = get_biz(slug, user, session)
    cards = session.exec(select(Card).where(Card.business_id == biz.id).order_by(Card.full_name)).all()
    return templates.TemplateResponse("cards_admin.html", {
        "request": request, "user": user, "biz": biz, "cards": cards, "base_url": str(request.base_url)
    })

# ══════════════════════════════════════════════════════════════════════════════
# ADMIN — Create / edit card
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/admin/{slug}/cards/new", response_class=HTMLResponse)
def new_card_page(slug: str, request: Request, session: Session = Depends(get_session)):
    user = require_user(request, session)
    biz = get_biz(slug, user, session)
    return templates.TemplateResponse("card_edit.html", {"request": request, "user": user, "biz": biz, "card": None, "card_links": [], "error": None})

@app.post("/admin/{slug}/cards/new")
async def create_card(
    slug: str, request: Request,
    full_name: str = Form(...), card_slug: str = Form(...),
    title: Optional[str] = Form(None), phone: Optional[str] = Form(None),
    email: Optional[str] = Form(None), bio: Optional[str] = Form(None),
    accent_color: Optional[str] = Form(None), vcard_enabled: Optional[str] = Form(None),
    photo_file: Optional[UploadFile] = File(None),
    session: Session = Depends(get_session),
):
    user = require_user(request, session)
    biz = get_biz(slug, user, session)
    slug_clean = card_slug.lower().strip().replace(" ", "-")
    if session.exec(select(Card).where(Card.business_id == biz.id, Card.slug == slug_clean)).first():
        return templates.TemplateResponse("card_edit.html", {"request": request, "user": user, "biz": biz, "card": None, "card_links": [], "error": f"Slug '{slug_clean}' already exists."})
    photo = await save_upload(photo_file) if photo_file and photo_file.filename else None
    c = Card(slug=slug_clean, full_name=full_name, title=title or None, phone=phone or None,
             email=email or None, bio=bio or None,
             accent_color=accent_color if accent_color and accent_color != biz.accent_color else None,
             vcard_enabled=(vcard_enabled == "1"), photo_path=photo, business_id=biz.id)
    session.add(c); session.commit()
    return RedirectResponse(f"/admin/{slug}/cards/{c.id}/edit", 302)

@app.get("/admin/{slug}/cards/{card_id}/edit", response_class=HTMLResponse)
def edit_card_page(slug: str, card_id: int, request: Request, session: Session = Depends(get_session)):
    user = require_user(request, session)
    biz = get_biz(slug, user, session)
    card = session.get(Card, card_id)
    if not card or card.business_id != biz.id: raise HTTPException(404)
    card_links = session.exec(select(CardLink).where(CardLink.card_id == card.id).order_by(CardLink.sort_order)).all()
    base = str(request.base_url)
    card_qr = make_qr(f"{base}c/{slug}/{card.slug}")
    return templates.TemplateResponse("card_edit.html", {
        "request": request, "user": user, "biz": biz,
        "card": card, "card_links": card_links, "error": None,
        "card_qr": card_qr, "base_url": base,
    })

@app.post("/admin/{slug}/cards/{card_id}/edit")
async def save_card(
    slug: str, card_id: int, request: Request,
    full_name: str = Form(...), card_slug: str = Form(...),
    title: Optional[str] = Form(None), phone: Optional[str] = Form(None),
    email: Optional[str] = Form(None), bio: Optional[str] = Form(None),
    accent_color: Optional[str] = Form(None), vcard_enabled: Optional[str] = Form(None),
    photo_file: Optional[UploadFile] = File(None), remove_photo: Optional[str] = Form(None),
    session: Session = Depends(get_session),
):
    user = require_user(request, session)
    biz = get_biz(slug, user, session)
    card = session.get(Card, card_id)
    if not card or card.business_id != biz.id: raise HTTPException(404)
    slug_clean = card_slug.lower().strip().replace(" ", "-")
    if slug_clean != card.slug:
        if session.exec(select(Card).where(Card.business_id == biz.id, Card.slug == slug_clean)).first():
            cl = session.exec(select(CardLink).where(CardLink.card_id == card.id).order_by(CardLink.sort_order)).all()
            base = str(request.base_url)
            return templates.TemplateResponse("card_edit.html", {"request": request, "user": user, "biz": biz, "card": card, "card_links": cl, "error": f"Slug '{slug_clean}' already exists.", "card_qr": make_qr(f"{base}c/{slug}/{card.slug}"), "base_url": base})
    if photo_file and photo_file.filename:
        del_upload(card.photo_path); card.photo_path = await save_upload(photo_file)
    elif remove_photo == "1":
        del_upload(card.photo_path); card.photo_path = None
    card.slug = slug_clean; card.full_name = full_name; card.title = title or None
    card.phone = phone or None; card.email = email or None; card.bio = bio or None
    card.accent_color = accent_color if accent_color and accent_color != biz.accent_color else None
    card.vcard_enabled = (vcard_enabled == "1")
    session.add(card); session.commit()
    return RedirectResponse(f"/admin/{slug}/cards/{card_id}/edit", 302)

@app.post("/admin/{slug}/cards/{card_id}/delete")
def delete_card(slug: str, card_id: int, request: Request, session: Session = Depends(get_session)):
    user = require_user(request, session)
    biz = get_biz(slug, user, session)
    card = session.get(Card, card_id)
    if card and card.business_id == biz.id:
        for cl in session.exec(select(CardLink).where(CardLink.card_id == card.id)).all(): session.delete(cl)
        del_upload(card.photo_path); session.delete(card); session.commit()
    return RedirectResponse(f"/admin/{slug}/cards", 302)

@app.post("/admin/{slug}/cards/{card_id}/links/add")
async def add_card_link(slug: str, card_id: int, request: Request, label: str = Form(...), url: str = Form(...), icon: str = Form("link"), session: Session = Depends(get_session)):
    user = require_user(request, session)
    biz = get_biz(slug, user, session)
    card = session.get(Card, card_id)
    if not card or card.business_id != biz.id: raise HTTPException(404)
    n = len(session.exec(select(CardLink).where(CardLink.card_id == card.id)).all())
    session.add(CardLink(label=label, url=url, icon=icon, sort_order=n, card_id=card.id)); session.commit()
    return RedirectResponse(f"/admin/{slug}/cards/{card_id}/edit", 302)

@app.post("/admin/{slug}/cards/{card_id}/links/{link_id}/delete")
def delete_card_link(slug: str, card_id: int, link_id: int, request: Request, session: Session = Depends(get_session)):
    user = require_user(request, session)
    biz = get_biz(slug, user, session)
    cl = session.get(CardLink, link_id)
    if cl and cl.card_id == card_id: session.delete(cl); session.commit()
    return RedirectResponse(f"/admin/{slug}/cards/{card_id}/edit", 302)

@app.get("/")
def root(): return RedirectResponse("/login", 302)

@app.get("/wallet/{biz_slug}/{card_slug}")
def wallet_redirect(biz_slug: str, card_slug: str):
    return RedirectResponse(url=f"/c/{biz_slug}/{card_slug}?wallet=1")
