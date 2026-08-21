# Dash MFB - Operations Portal

Internal work tracking for the Operations department. Staff raise pieces of
work, do them against a fixed checklist, and hand them up for sign-off.
Managers see what is waiting on them and what is running late.

> **Status:** feature complete for internal testing. Not yet ready for real
> customer data - see [Before deploying](#before-deploying).

---

## Requirements

- **Python 3.10+** (developed on 3.14)
- pip

Nothing else for local work: the database is SQLite and needs no server.
Production needs Postgres and a cron daemon.

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
cp .env.example .env               # then edit it, see "Configuration" below

# 4. Build the database
python manage.py migrate

# 5. Create the first account (it is given the Admin role automatically)
python manage.py createsuperuser

# 6. Run it
python manage.py runserver
```

Open **http://127.0.0.1:8000/** and sign in with the **email address** you gave
to `createsuperuser`. Usernames are deliberately not accepted at the login form.

`db.sqlite3` is gitignored, so a fresh clone starts empty: steps 4 and 5 are
not optional.

### Then set the department up, in this order

There is a dependency chain. A team needs a lead, and only someone already
holding a leadership role can be picked as one, but the staff form offers teams
as tick-boxes, which is no use before any team exists.

1. **Onboard the managers** at `/app/staff/new/`, leaving teams empty
2. **Create the teams** at `/app/teams/` - the lead is added to their own team
   automatically, which matters because a task's assignee has to belong to the
   team it is raised under
3. **Onboard everyone else**, ticking their teams as you go
4. **Build the process catalog** at `/app/catalog/new/` - nothing can be raised
   until at least one process type exists

---

## How the work flows

```
raised → started → checklist ticked → submitted
       → [Team Lead] → [Department Head] → completed
```

The bracketed steps come from the process type. A reviewer can send anything
back with a reason, which returns it to the assignee with the history intact.

### Roles

| Role | Can |
| --- | --- |
| Operations Staff | Raise and do work, hand it to another team |
| Team Lead | The above, plus sign off their team's work, onboard staff, manage teams and the catalog |
| Department Head | The above across every team, plus final sign-off |
| Admin | Everything, plus the Django admin |

### Two rules worth knowing before you change anything

**Nobody closes their own work.** There is no "no approval needed" option on a
process type. A deadline is a standing incentive to mark something finished
that is not, so every task needs a sign-off from somebody else. A Team Lead's
own task escalates to the Department Head rather than closing. The Head is the
single exception, because nobody sits above them here.

**Nothing is ever deleted.** Tasks are archived, teams and staff are retired.
`Task.team` and `Task.process_type` are `PROTECT` precisely so the record of
who approved what cannot be erased by tidying up.

### The process catalog

Each entry fixes three things so the person doing the work does not decide
them: the turnaround target, the standard checklist, and who signs it off.

A fourth, a permission gate that had to be cleared before work could start,
was **withdrawn after the demo**. `ProcessType.requires_authorisation` and the
two `auth_*` stages survive as columns so the sign-off history on tasks that
went through the gate still reads correctly, but nothing arms them:
`Task.needs_authorisation` returns False regardless, and
`approvals.opening_stage` always returns `STAGE_DRAFT`. Those two functions are
where to look if it is ever reinstated.

The checklist is **copied onto each task when it is raised and frozen there**.
Editing a process later never shifts ticks under somebody mid-job.

`Ad hoc request` exists as the escape hatch for one-off work that fits no
other entry. If it becomes the most-used type, that is the catalog telling you
something real is missing - the task titles inside it will say what.

### The two clocks

Turnaround is measured as two separate obligations, not one number:

- **Time to submit** - from when work could start to handing in.
- **Time in review** - from handing in to final sign-off.

`staff_late` judges the person who did the work; `finished_late` is the overall
figure. Rolling them together blames whoever was assigned for however long
their manager sat on it.

---

## Configuration

All secrets live in `.env`, which is gitignored. `.env.example` is the
committed template; copy it and fill in real values.

### Required in production

| Variable | What it does |
| --- | --- |
| `DJANGO_SECRET_KEY` | App refuses to boot with `DEBUG=False` and no key |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hostnames Django will answer for |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Full origins, e.g. `https://portal.dash-mfb.com` |
| `SITE_URL` | **Every link in every email is built from this.** Cron has no request to derive it from, so leaving the default points all of them at localhost |
| `DB_ENGINE`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | Defaults to local SQLite; move to Postgres |

### Email, through Microsoft Graph

| Variable | What it does |
| --- | --- |
| `MS_GRAPH_TENANT_ID` | Azure AD tenant ID |
| `MS_GRAPH_CLIENT_ID` | App registration (client) ID |
| `MS_GRAPH_CLIENT_SECRET` | App registration client secret |
| `MS_GRAPH_SENDER` | Mailbox that sends, e.g. `robot@dash-mfb.com` |

Office 365 tenants normally have SMTP AUTH disabled, so Django's SMTP backend
cannot authenticate. The Graph implementation is in
[`users/email_backends.py`](users/email_backends.py). The app registration
needs the **`Mail.Send` _application_ permission** (not the delegated one) with
**admin consent granted**, and the sender must be a real mailbox.

**Leave these blank and emails print to the `runserver` terminal instead.**
That is the right setup for development: invite links and reminders still work,
copied out of the console, and nothing real is sent by accident.

### Reminders and digests

| Variable | Default | What it does |
| --- | --- | --- |
| `ASSIGNMENT_EMAILS_ENABLED` | `True` | Email somebody the moment a task is assigned to them |
| `REMINDERS_ENABLED` | `True` | Master switch for chasing assignees |
| `REMINDER_MODE` | `milestones` | `milestones` scales to each task's own deadline; `interval` is a fixed clock |
| `REMINDER_MILESTONES` | `50,80` | Percentages of the way to the deadline at which to email |
| `REMINDER_FINAL_MINUTES` | `15` | The one-off last call |
| `REMINDER_OVERDUE_EVERY_MINUTES` | `1440` | Late work is chased daily, not hourly |
| `REMINDER_HOURS` | `8-18` | Routine reminders are held outside these hours; `0-0` disables the quiet period |
| `REMINDER_EVERY_MINUTES` / `REMINDER_MAX_PER_TASK` | `30` / `8` | `interval` mode only |
| `APPROVAL_DIGESTS_ENABLED` | `True` | Master switch for telling managers what is waiting |
| `APPROVAL_DIGEST_MIN_GAP_MINUTES` | `60` | Shortest gap between two digests to one person |
| `APPROVAL_DIGEST_EVERY_MINUTES` | `240` | How often to repeat while work is still unactioned |

### Everything else

| Variable | Default | What it does |
| --- | --- | --- |
| `DJANGO_DEBUG` | `True` | Turn off in production |
| `DJANGO_TIME_ZONE` | `Africa/Lagos` | Display timezone; storage is always UTC |
| `PAGE_SIZE` | `25` | Rows per page on every list |
| `MAX_UPLOAD_MB` | `10` | Largest single attachment. **nginx caps bodies at 1 MB by default**, so raise `client_max_body_size` to match |
| `DJANGO_USE_PROXY_SSL_HEADER` | `False` | Required behind a load balancer, or the SSL redirect loops forever |
| `DJANGO_SECURE_HSTS_SECONDS` | `0` | Start at `3600`, raise once HTTPS is proven on every subdomain |
| `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` | `False` | Only once every subdomain is HTTPS. Hard to undo |
| `DJANGO_SECURE_HSTS_PRELOAD` | `False` | Submitting to the browser preload list is close to permanent |
| `DB_CONN_MAX_AGE` | `60` | Seconds a Postgres connection is reused. Ignored on SQLite |
| `DJANGO_SECURE_SSL_REDIRECT`, `DJANGO_SESSION_COOKIE_SECURE`, `DJANGO_CSRF_COOKIE_SECURE` | on when `DEBUG` is off | Overrides, e.g. while TLS is still being set up |

---

## What exists

| URL | Purpose |
| --- | --- |
| `/` | Sign in (email only) |
| `/forgot-password/` | Request a reset link |
| `/reset/<uidb64>/<token>/` | Choose a new password |
| `/invite/<uidb64>/<token>/` | New starter sets their first password |
| `/app/` | Lands on whichever queue most likely needs you |
| `/app/q/<key>/` | A work queue: `my-work`, `authorise`, `awaiting`, `submitted`, `team`, `completed`, `archived` |
| `/app/tasks/<id>/` | One task: checklist, files, comments, sign-off trail |
| `/app/catalog/` | Process catalog, editable by management |
| `/app/staff/` | Onboard, edit, resend invite, deactivate |
| `/app/teams/` | Create, edit, retire teams |
| `/app/analytics/` | Turnaround and volume, split by the two clocks |
| `/app/files/<id>/` | Attachment download, permission-checked |
| `/admin/` | Django admin |

### Accounts

People sign in with their **corporate email address only**. Anything not
ending `@dash-mfb.com` is rejected, enforced by a `pre_save` signal in
[`users/models.py`](users/models.py) so it holds on every path including
`createsuperuser`.

Staff never receive a password. An admin onboards them and the portal emails a
single-use invite link, valid for 7 days, which they use to set their own.

Sessions expire after **15 minutes** of inactivity. Reset links last 3 hours.

### Attachments

Multiple images or documents per task, up to `MAX_UPLOAD_MB` each. Stored under
a random filename with the original kept on the row, so nothing can be found by
guessing a URL.

**There is deliberately no `MEDIA_URL` and nothing serves the upload directory.**
Every download goes through `portal.views.attachment_download`, which checks who
is asking. Adding `static()`/`MEDIA_URL` serving for that path would put
customer documents on the open internet.

---

## Layout

```
Operations_Portal/
├── Operations_Portal/          # settings and root URLconf
├── portal/
│   ├── models.py               # ProcessType, Task, Approval, Attachment, Comment
│   ├── queues.py               # the sidebar queues, and who may see what
│   ├── approvals.py            # every sign-off and permission rule
│   ├── reminders.py            # who to chase about a deadline, and when
│   ├── digests.py              # what is waiting on each manager
│   ├── notify.py               # the immediate emails, none of which can raise
│   ├── pagination.py           # one pager, used by every list view
│   ├── management/commands/    # send_task_reminders, send_approval_digests
│   ├── static/css/portal.css   # the whole design system
│   ├── static/vendor/          # Remix Icon, subset and vendored
│   └── templates/portal/
├── users/
│   ├── models.py               # Team, Profile, the corporate-email signal
│   ├── backends.py             # email-only login
│   ├── email_backends.py       # Microsoft Graph
│   └── templates/users/        # login, invite, password reset
├── .env                        # gitignored, create from .env.example
└── manage.py
```

Business rules live in `queues.py`, `approvals.py`, `reminders.py` and
`digests.py`, kept apart from the views so they can be tested without HTTP or a
mail server.

---

## Tests

```bash
python manage.py test
```

`portal/tests.py` covers the withdrawn permission gate, including the cases
that made removing it by hand go wrong: a flag forced straight into the
database, the routes, the queue, and whether historical sign-offs still render.

---

## Icons

Remix Icon is vendored in `portal/static/vendor/remixicon/`, subset to only the
icons this app uses: 2.5 KB of font instead of 185 KB, and no external request.

It used to load from a CDN. On any network that blocks external hosts, which
is most corporate ones, every icon in the portal disappeared.

To add one, reference it in a template or in Python, then regenerate:

```bash
pip install fonttools brotli
curl -o /tmp/ri.css   https://cdn.jsdelivr.net/npm/remixicon@4.9.0/fonts/remixicon.css
curl -o /tmp/ri.woff2 https://cdn.jsdelivr.net/npm/remixicon@4.9.0/fonts/remixicon.woff2
# look up the codepoint for the new class in /tmp/ri.css, add it to the
# --unicodes list, rerun pyftsubset, then add the rule to remixicon.css
```

**Scan `.py` as well as `.html`.** The sidebar builds its icon list in
`portal/queues.py`, so a subset generated from templates alone silently drops
seven icons and the whole Work section loses them.

Bump `ASSET_VERSION` after any change, or browsers keep the old font.

---

## Emails sent as things happen

Three moments are emailed from the request itself rather than waiting for the
cron round, because each leaves somebody standing still until they hear. They
live in [`portal/notify.py`](portal/notify.py).

| Moment | Who is told | What it carries |
| --- | --- | --- |
| Work assigned | the new assignee | who assigned it, the deadline, the checklist size, any notes |
| Work sent to a team, unowned | that team's Team Lead | who sent it, the deadline, and that nobody is on it yet |
| Handed in for sign-off | the reviewer | who submitted it, how long they took, whether that was late |
| Decision made | the assignee | signed off, passed up a level, or sent back with the reason |

A handover arrives unowned on purpose, so there is no assignee to email. The
receiving Team Lead is told instead, because otherwise the handover is silent
and they find out from the digest hours later while the deadline runs.

**Assigning to yourself sends nothing** - you know what you just gave yourself,
and a staff member raising their own work would otherwise be emailed about it
every time. Reassigning emails only the new person; whoever was taken off is
not told.

**Assigning from the Django admin sends nothing.** These hook the two views
that can set an assignee, not a `post_save` signal. A signal would catch every
path but would also fire on bulk saves, so one management command could email
the whole department.

**None of these can raise.** A dead mail server must never undo a sign-off that
already happened, so every failure is logged and swallowed. The digests below
are the safety net: anything missed still turns up there.

### How the emails are built

Every message goes out as **both plain text and HTML**. The text part is not
decoration: some clients refuse HTML, some people read mail in a terminal, and
a message with no text alternative scores worse with spam filters.

```
portal/templates/email/base.html    the shell every message extends
portal/templates/email/_fact.html   one label/value row
portal/templates/email/_note.html   a callout: a reason, a warning
<name>_subject.txt                  the subject line
<name>_email.txt                    the plain text part
<name>_email.html                   the HTML part, extends email/base.html
```

The HTML is tables and inline styles **on purpose**. Email clients are not
browsers: Outlook renders with Word, Gmail strips much of a `<style>` block,
and flex and grid are unreliable almost everywhere. Nothing external is loaded,
because images and webfonts are blocked by default in most clients, so the
icon font and stylesheet the app uses are no help here.

Two details worth not undoing:

- The shell is a fluid table with `max-width:600px`, wrapped in an
  `[if mso]` fixed-width table. Word ignores `max-width`, so Outlook needs its
  own cage or the layout runs full-bleed on a wide monitor.
- Long values such as an email address are one unbreakable token, so the fact
  cells set `overflow-wrap`. Without it a single address sets the table's
  minimum width and the whole message overflows on a phone.

To see them without sending anything, leave the `MS_GRAPH_*` variables blank
and Django prints each message, both parts, to the `runserver` terminal.

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
- already submitted for review (the assignee cannot move it, so chasing them
  is noise)
- unassigned, or assigned to a deactivated account

Overdue tasks are chased once a day, not every few minutes. Routine reminders
are held back outside `REMINDER_HOURS` so an overnight deadline does not email
anyone at 3am; the final warning ignores that, which is its purpose.

A task sent back by a reviewer gets its reminder allowance back, since the
work is starting again.

### Telling managers what is waiting on them

`send_approval_digests` is the other half. Work stuck waiting for sign-off is
burning its turnaround time while the person who could release it has no idea,
so each Team Lead and Department Head is emailed the contents of their Sign-off
queue.

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
