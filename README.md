# ✦ Glint — Review Aggregation Platform

**Turn every customer interaction into a 5-star review.**  
Ghost-design landing pages + real-time analytics + private feedback interception.

---

## Quick Start

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Open `http://localhost:8000` → you'll be redirected to `/login`.

**First time?** Go to `/register` to create your account, then create your first business.

---

## URL Structure

| Route | Description |
|-------|-------------|
| `/register` | Create an account |
| `/login` | Sign in |
| `/dashboard` | All your businesses |
| `/new` | Create a new business |
| `/admin/{slug}` | Dashboard with Pulse chart & analytics |
| `/settings/{slug}` | Edit business: name, logo, color, links |
| `/s/{slug}` | 📱 Public landing page (QR code target) |
| `/click/{id}` | Click tracking + redirect |
| `/feedback/{slug}` | Private feedback endpoint (JSON POST) |

---

## Email Notifications for Feedback

To receive email alerts when customers send private feedback:

1. Set `feedback_email` in the Settings page for your business.
2. Configure your SMTP server via environment variables:

```bash
export SMTP_HOST="smtp.gmail.com"
export SMTP_PORT="587"
export SMTP_USER="you@gmail.com"
export SMTP_PASS="your-app-password"
```

For Gmail: generate an App Password at myaccount.google.com/apppasswords.

---

## Security Note

Set a strong secret before deploying:

```bash
export GLINT_SECRET="your-random-64-char-secret-here"
```

---

## File Structure

```
glint/
├── main.py              # All routes + business logic
├── models.py            # SQLModel: User, Business, ReviewLink, ScanEvent, FeedbackItem
├── auth.py              # Session cookie auth (no external dependencies)
├── requirements.txt
├── glint.db             # SQLite (auto-created on first run)
└── templates/
    ├── auth.html        # Login + Register (shared template)
    ├── dashboard.html   # Business overview for logged-in user
    ├── business_form.html  # Create + Edit settings (shared template)
    ├── index.html       # Ghost Design public landing page
    └── admin.html       # Analytics dashboard
```

## Roadmap / Natural Upsells

- **Multi-location**: one account, multiple slugs per location
- **Weekly digest email**: auto-send scan + conversion stats every Monday
- **Scan & Win**: gamified review incentive (enter draw after review)
- **White-label QR boards**: matte black acrylic stands, €25 per unit
