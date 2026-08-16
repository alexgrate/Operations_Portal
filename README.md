# Dash MFB - Operations Portal

A Django app for logging, tracking, and managing operational tasks across the
team: a task board, a process catalog, and turnaround analytics.

> **Status:** authentication is complete and working. The dashboard is still a
> front-end shell - see [Current state](#current-state) before you start.

---

## Requirements

- **Python 3.10+** (developed on 3.14)
- pip

Nothing else - the database is SQLite and needs no separate server.

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
cp .env.example .env               # then edit it - see "Configuration" below

# 4. Set up the database
python manage.py migrate

# 5. Create an admin account
python manage.py createsuperuser

# 6. Run it
python manage.py runserver
```

Open **http://127.0.0.1:8000/**.

`db.sqlite3` is gitignored, so a fresh clone starts with an empty database -
step 4 and step 5 are not optional.

---

## Configuration

All secrets live in `.env`, which is gitignored. `.env.example` is the
committed template; copy it and fill in real values.

| Variable | Required | What it does |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | **production** | App refuses to boot with `DEBUG=False` and no key |
| `DJANGO_DEBUG` | no | `True` / `False`, defaults to `True` |
| `DJANGO_ALLOWED_HOSTS` | **production** | Comma-separated hostnames Django will answer for |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | **HTTPS** | Full origins, e.g. `https://portal.dash-mfb.com` |
| `DJANGO_TIME_ZONE` | no | Display timezone; set `Africa/Lagos` for local times |
| `DJANGO_USE_PROXY_SSL_HEADER` | behind a proxy | Without it, the SSL redirect loops forever |
| `DJANGO_SECURE_HSTS_SECONDS` | no | Start at `3600`, raise once HTTPS is proven |
| `DJANGO_SECURE_SSL_REDIRECT` / `..._SESSION_COOKIE_SECURE` / `..._CSRF_COOKIE_SECURE` | no | Default to **on** whenever `DEBUG` is off |
| `DB_ENGINE`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | **production** | Defaults to local SQLite; move to Postgres |
| `MS_GRAPH_TENANT_ID` | for real email | Azure AD tenant ID |
| `MS_GRAPH_CLIENT_ID` | for real email | App registration (client) ID |
| `MS_GRAPH_CLIENT_SECRET` | for real email | App registration client secret |
| `MS_GRAPH_SENDER` | for real email | Mailbox that sends, e.g. `robot@dash-mfb.com` |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | optional | Blank hides the "Continue with Google" button |

`.env.example` documents all of these with inline guidance.

### Google sign-in

Optional. Create an OAuth 2.0 Client ID (Web application) in the Google Cloud
console with the redirect URI
`https://<your-host>/accounts/google/login/callback/`, then set
`GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. Credentials are read from the
environment, so no `django.contrib.sites` row and nothing secret in the
database. Only `@dash-mfb.com` Google accounts are accepted - enforced in
[`portal/adapters.py`](portal/adapters.py).

**Leave the two variables blank and the button is hidden entirely** - it is
not shown in a broken state.

### Approvals

A process type can be marked **"Needs approval"** in the catalog. Tasks of
that type do not complete when they reach a "Completes" column - they park in
**Awaiting approval** until an approver signs them off, and only then get a
`completed_at`.

Approvers are Django staff/superusers, plus anyone in an **`Admin`** or
**`Team Lead`** group (create the groups in `/admin/`). Re-opening an approved
task clears the sign-off, so it has to be approved again.

### Email

Password-reset email goes out through the **Microsoft Graph API**, not SMTP -
Office 365 tenants normally have SMTP AUTH disabled, so Django's SMTP backend
can't authenticate. The implementation is in
[`users/email_backends.py`](users/email_backends.py).

The Azure app registration needs the **`Mail.Send` _application_ permission**
(not the delegated one) with **admin consent granted**, and `MS_GRAPH_SENDER`
must be a real mailbox in the tenant.

**If you leave the `MS_GRAPH_*` variables blank**, the app falls back to
printing emails to the terminal running `runserver`. Password reset still works
locally - copy the link out of the console output. That is the recommended
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
username, so accounts created before this convention - and anything made with
`createsuperuser` - can still sign in.

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
├── .env                   # secrets - gitignored, create from .env.example
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
- **No models.** `portal/models.py` is empty - there is no Task, ProcessType, or
  Column model, and nothing persists.
- **"Continue with Google"** on the login page is decorative; there is no OAuth.
- `portal/static/css/register.css` is an empty file and is not loaded.

---

## Deadline reminders

Assignees are emailed as their task's deadline approaches. Nothing runs on a
timer inside Django, so this needs a cron entry:

```cron
*/5 * * * * cd /srv/operations-portal && .venv/bin/python manage.py send_task_reminders  >> /var/log/portal-mail.log 2>&1
*/5 * * * * cd /srv/operations-portal && .venv/bin/python manage.py send_approval_digests >> /var/log/portal-mail.log 2>&1
```

Running both every 5 minutes is right. Each command works out what is actually
due, so extra runs send nothing and a missed run catches up.

See what they would do without sending anything:

```bash
python manage.py send_task_reminders   --dry-run
python manage.py send_approval_digests --dry-run
```

**Set `SITE_URL`.** Cron has no web request to work out the site address from,
so every link in every reminder is built from that setting. Left at its
default, all of them point at `localhost`.

### Who gets chased, and when

By default (`REMINDER_MODE=milestones`) each task produces three emails,
placed as fractions of its own deadline rather than on a fixed clock:

| | 24h target | 2h target |
|---|---|---|
| Halfway | after 12h | after 1h |
| 80% gone | after 19h 12m | after 1h 36m |
| Last call | 15 min left | 15 min left |

A fixed 30-minute clock is available as `REMINDER_MODE=interval`, but on a
24-hour target it sends 48 emails per task. People filter that to junk, and
the final warning goes with it.

Nobody is emailed about a task that is:

- completed or archived
- waiting on a manager's permission, or already submitted for review
  (the assignee cannot move it, so chasing them is noise)
- unassigned, or assigned to a deactivated account

Overdue tasks are chased once a day, not every few minutes. Routine reminders
are held back outside `REMINDER_HOURS` so an overnight deadline does not email
anyone at 3am; the final warning ignores that, which is its purpose.

A task sent back by a reviewer gets its reminder allowance back, since the
work is starting again.

### Telling managers what is waiting on them

`send_approval_digests` is the other half. Work stuck waiting for permission
or sign-off is burning its turnaround time while the person who could release
it has no idea, so each Team Lead and Department Head is emailed the contents
of their Permission and Sign-off queues.

**One email per manager, not one per task.** A Department Head covering
several teams would otherwise be buried, and the point is to be read.

A digest goes out when something new arrives, no more often than
`APPROVAL_DIGEST_MIN_GAP_MINUTES` (default 60), and otherwise repeats every
`APPROVAL_DIGEST_EVERY_MINUTES` (default 240) while anything is still sitting
there. Tightest deadline first, new arrivals marked `NEW`, overdue flagged in
the subject line. Nothing waiting means no email at all.

The list comes from the same `portal/queues.py` functions the sidebar uses, so
the email can never disagree with what the manager sees on screen.

---

## Before deploying

Everything below is driven from `.env` - see the table above.

1. `DJANGO_DEBUG=False` and a real `DJANGO_SECRET_KEY`
   (the app refuses to start without one)
2. `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS`
3. Postgres via the `DB_*` variables - **SQLite cannot handle concurrent
   writers** and will lock up under real use
4. `DJANGO_USE_PROXY_SSL_HEADER=True` if TLS terminates at a load balancer
5. `python manage.py collectstatic` - `STATIC_ROOT` is set to `staticfiles/`.
   With `DEBUG=False` Django serves no static files itself, so put nginx or
   WhiteNoise in front
6. `python manage.py migrate`
7. `SITE_URL` and the cron entry for reminders - see the section above.
   Reminder emails do nothing until cron is running

The HTTPS settings (SSL redirect, secure session and CSRF cookies) turn
themselves **on** as soon as `DEBUG` is off - nothing to remember.

Verify with:

```bash
python manage.py check --deploy
```

The only remaining warning should be `security.W021` (HSTS preload), which is
deliberately opt-in: submitting to the preload list is very hard to reverse.
