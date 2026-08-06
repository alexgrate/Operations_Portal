# Dash MFB — Operations Portal

A Django app for logging, tracking, and managing operational tasks across the
team: a task board, a process catalog, and turnaround analytics.

> **Status:** authentication is complete and working. The dashboard is still a
> front-end shell — see [Current state](#current-state) before you start.

---

## Requirements

- **Python 3.10+** (developed on 3.14)
- pip

Nothing else — the database is SQLite and needs no separate server.

---

## Setup

From a fresh clone:

```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create your local config
cp .env.example .env               # then edit it — see "Configuration" below

# 4. Set up the database
python manage.py migrate

# 5. Create an admin account
python manage.py createsuperuser

# 6. Run it
python manage.py runserver
```

Open **http://127.0.0.1:8000/**.

`db.sqlite3` is gitignored, so a fresh clone starts with an empty database —
step 4 and step 5 are not optional.

---

## Configuration

All secrets live in `.env`, which is gitignored. `.env.example` is the
committed template; copy it and fill in real values.

| Variable | Required | What it does |
| --- | --- | --- |
| `MS_GRAPH_TENANT_ID` | for real email | Azure AD tenant ID |
| `MS_GRAPH_CLIENT_ID` | for real email | App registration (client) ID |
| `MS_GRAPH_CLIENT_SECRET` | for real email | App registration client secret |
| `MS_GRAPH_SENDER` | for real email | Mailbox that sends, e.g. `robot@dash-mfb.com` |
| `DJANGO_SECRET_KEY` | production | Falls back to an insecure dev key if unset |
| `DJANGO_DEBUG` | no | `True` / `False`, defaults to `True` |

### Email

Password-reset email goes out through the **Microsoft Graph API**, not SMTP —
Office 365 tenants normally have SMTP AUTH disabled, so Django's SMTP backend
can't authenticate. The implementation is in
[`users/email_backends.py`](users/email_backends.py).

The Azure app registration needs the **`Mail.Send` _application_ permission**
(not the delegated one) with **admin consent granted**, and `MS_GRAPH_SENDER`
must be a real mailbox in the tenant.

**If you leave the `MS_GRAPH_*` variables blank**, the app falls back to
printing emails to the terminal running `runserver`. Password reset still works
locally — copy the link out of the console output. That is the recommended
setup for development, so you never send real mail by accident.

---

## What exists

| URL | Purpose |
| --- | --- |
| `/` | Log in |
| `/register/` | Create an account |
| `/forgot-password/` | Request a password-reset link |
| `/forgot-password/sent/` | "Check your email" confirmation |
| `/reset/<uidb64>/<token>/` | Choose a new password |
| `/reset/done/` | Reset complete |
| `/dashboard/` | Task board, process catalog, analytics (login required) |
| `/logout/` | Log out (POST only) |
| `/admin/` | Django admin |

### Accounts

People sign in with their **corporate email**. On registration the email is
stored as both `User.email` and `User.username`.

A custom backend
([`users/backends.py`](users/backends.py)) accepts **either** an email or a
username, so accounts created before this convention — and anything made with
`createsuperuser` — can still sign in.

Sessions expire after **15 minutes of inactivity**
(`SESSION_COOKIE_AGE` + `SESSION_SAVE_EVERY_REQUEST`).

Password-reset links are single-use and expire after **3 hours**
(`PASSWORD_RESET_TIMEOUT`).

---

## Layout

```
Operations_Portal/
├── Operations_Portal/     # project settings and root URLconf
├── portal/                # login/logout, dashboard, shared static files
│   ├── static/css/        # login.css, dashboard.css, register.css (empty)
│   ├── static/images/     # logos
│   └── templates/portal/  # base.html, dashboard.html
├── users/                 # registration, auth backend, Graph email backend
│   └── templates/users/   # base2.html, login, register, password-reset pages
├── .env                   # secrets — gitignored, create from .env.example
└── manage.py
```

Both apps' CSS lives in `portal/static/`; the auth pages share `login.css`
through `users/templates/users/base2.html`.

---

## Current state

**Working:** registration with validation, login by email or username, logout,
login-required dashboard, the full password-reset flow, and flash messages on
every auth page.

**Not built yet:**

- **The dashboard is an empty shell.** `portal/templates/portal/dashboard.html`
  is driven entirely by JavaScript that fills `#boardColumns`, `#catalogList`,
  `#statGrid`, `#byProcessTable`, and `#byStaffTable`. That JavaScript does not
  exist, so the board, catalog, and analytics tabs render empty. The tab
  switcher and modals are also inert.
- **No models.** `portal/models.py` is empty — there is no Task, ProcessType, or
  Column model, and nothing persists.
- **"Continue with Google"** on the login page is decorative; there is no OAuth.
- `portal/static/css/register.css` is an empty file and is not loaded.

---

## Before deploying

`settings.py` is still development-shaped. At minimum:

- Set `DJANGO_DEBUG=False` and a real `DJANGO_SECRET_KEY` in the environment
- Fill in `ALLOWED_HOSTS`
- Serve static files properly (`python manage.py collectstatic`)
- Move off SQLite
- Add `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`

Run `python manage.py check --deploy` for the full list.
