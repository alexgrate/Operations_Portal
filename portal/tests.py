"""Tests for the portal app.

Run with:  python manage.py test portal
"""
import io
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from users.models import Profile, Team

from . import approvals, digests, queues, reminders
from .forms import ProcessTypeForm
from .models import Approval, Attachment, AttachmentAccess, ProcessType, Task

PW = 'Testing!2345'


def make_user(slug, role, first):
    email = f'{slug}@dash-mfb.com'
    user = User.objects.create_user(username=email, email=email, password=PW,
                                    first_name=first, last_name='Test')
    user.profile.role = role
    user.profile.save()
    return user


class PermissionGateWithdrawnTests(TestCase):
    """The permission-before-work gate was withdrawn after the demo.

    These exist because removing it by hand left it half-alive once already:
    the queue that listed the work was commented out while the gate itself
    kept firing, so tasks landed in a stage nothing could move them on from.
    """

    def setUp(self):
        self.head = make_user('gate_head', Profile.ROLE_DEPT_HEAD, 'Chika')
        self.lead = make_user('gate_lead', Profile.ROLE_TEAM_LEAD, 'Ada')
        self.staff = make_user('gate_staff', Profile.ROLE_STAFF, 'Tunde')
        self.team = Team.objects.create(name='Gate Team', lead=self.lead)
        for user in (self.lead, self.staff):
            user.profile.teams.add(self.team)
        self.process = ProcessType.objects.create(
            name='Gate Process', target_hours=8, checklist=['Only item'],
            approval_level=ProcessType.APPROVAL_LEAD,
        )

    def client_for(self, user):
        client = self.client_class()
        self.assertTrue(client.login(username=user.email, password=PW))
        return client

    def test_no_process_type_can_arm_the_gate(self):
        self.assertNotIn('requires_authorisation', ProcessTypeForm().fields)

        self.client_for(self.head).post(reverse('process-type-create'), {
            'name': 'Sneaky', 'approval_level': 'lead', 'target_value': 4,
            'target_unit': 'hours', 'checklist_text': 'x',
            'requires_authorisation': 'on',
        })
        sneaky = ProcessType.objects.filter(name='Sneaky').first()
        self.assertIsNotNone(sneaky)
        self.assertFalse(sneaky.requires_authorisation)

    def test_a_flag_forced_into_the_database_is_inert(self):
        """The belt to the form's braces.

        A flag set by a fixture, an old import, or straight in the database
        must not be able to freeze a task.
        """
        ProcessType.objects.filter(pk=self.process.pk).update(requires_authorisation=True)
        self.process.refresh_from_db()
        self.assertTrue(self.process.requires_authorisation)

        task = Task.objects.create(title='Forced', process_type=self.process,
                                   assignee=self.staff, team=self.team,
                                   created_by=self.staff)
        approvals.apply_opening_state(task, self.staff)
        task.save()

        self.assertEqual(task.approval_stage, Task.STAGE_DRAFT)
        self.assertNotIn(task.approval_stage, Task.AWAITING_AUTH)
        self.assertFalse(task.needs_authorisation)
        self.assertTrue(approvals.can_start(self.staff, task))
        self.assertTrue(approvals.can_submit(self.staff, task))

    def test_the_routes_are_gone(self):
        task = Task.objects.create(title='Routes', process_type=self.process,
                                   assignee=self.staff, team=self.team,
                                   created_by=self.staff)
        for name in ('task-authorise', 'task-decline', 'task-request-auth'):
            with self.assertRaises(NoReverseMatch):
                reverse(name, kwargs={'pk': task.pk})

        client = self.client_for(self.lead)
        for path in ('authorise', 'decline', 'request-auth'):
            self.assertEqual(
                client.post(f'/app/tasks/{task.pk}/{path}/', {'comment': 'x'}).status_code, 404,
            )

    def test_the_permission_queue_is_gone(self):
        self.assertNotIn('authorise', queues.QUEUES)
        self.assertEqual(
            self.client_for(self.lead).get('/app/q/authorise/').status_code, 403,
        )

    def test_the_task_page_offers_no_permission_controls(self):
        task = Task.objects.create(title='Controls', process_type=self.process,
                                   assignee=self.staff, team=self.team,
                                   created_by=self.staff)
        page = self.client_for(self.lead).get(
            reverse('task-detail', kwargs={'pk': task.pk})).content.decode()
        for term in ('Permission to start', 'Awaiting permission', 'Permit', 'Refuse'):
            self.assertNotIn(term, page)

    def test_digests_carry_sign_off_work_only(self):
        task = Task.objects.create(title='Digest', process_type=self.process,
                                   assignee=self.staff, team=self.team,
                                   created_by=self.staff)
        task.checklist_done = {'0': True}
        task.save()
        approvals.submit(task)

        pending = digests.waiting_on(self.lead)
        self.assertTrue(all(t.approval_stage in Task.IN_REVIEW for t in pending))

        _, text, html = digests.build_email(self.lead, pending)
        for body in (text, html):
            self.assertNotIn('permission', body.lower())
            self.assertNotIn('/app/q/authorise/', body)

    def test_history_from_before_the_change_still_reads(self):
        """The stage constants stay so old sign-offs do not render blank."""
        task = Task.objects.create(title='Historical', process_type=self.process,
                                   assignee=self.staff, team=self.team,
                                   created_by=self.staff)
        Approval.objects.create(task=task, actor=self.lead,
                                stage=Approval.STAGE_AUTH_LEAD,
                                decision=Approval.DECISION_APPROVED,
                                comment='Permitted at the time.')

        self.assertEqual(task.approvals.first().stage_label, 'Team Lead, permission')

        page = self.client_for(self.lead).get(
            reverse('task-detail', kwargs={'pk': task.pk})).content.decode()
        self.assertIn('Team Lead, permission', page)
        self.assertIn('Permitted at the time.', page)


@override_settings(SITE_URL='https://portal.dash-mfb.com')
class AssignmentEmailTests(TestCase):
    """Somebody put work on your desk, so you get told."""

    def setUp(self):
        self.lead = make_user('as_lead', Profile.ROLE_TEAM_LEAD, 'Ada')
        self.staff = make_user('as_staff', Profile.ROLE_STAFF, 'Tunde')
        self.other = make_user('as_other', Profile.ROLE_STAFF, 'Segun')
        self.team = Team.objects.create(name='Assign Team', lead=self.lead)
        for user in (self.lead, self.staff, self.other):
            user.profile.teams.add(self.team)
        self.process = ProcessType.objects.create(
            name='Assign Process', target_hours=8, checklist=['One', 'Two'],
            approval_level=ProcessType.APPROVAL_LEAD,
        )
        mail.outbox = []

    def client_for(self, user):
        client = self.client_class()
        self.assertTrue(client.login(username=user.email, password=PW))
        return client

    def raise_task(self, actor, assignee, title='Onboard Chidera Nwosu'):
        self.client_for(actor).post(reverse('task-create'), {
            'title': title, 'process_type': self.process.pk,
            'team': self.team.pk, 'assignee': assignee.pk if assignee else '',
            'notes': 'Walk-in customer, branch referral.',
        })
        return Task.objects.filter(title=title).first()

    def test_assigning_to_somebody_else_emails_them(self):
        task = self.raise_task(self.lead, self.staff)

        self.assertIsNotNone(task)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [self.staff.email])
        self.assertIn('Assigned to you', message.subject)
        self.assertIn(task.title, message.subject)

    def test_the_email_says_what_they_need(self):
        task = self.raise_task(self.lead, self.staff)
        body = mail.outbox[0].body

        self.assertIn('Tunde Test', body)
        self.assertIn('Ada Test', body)
        self.assertIn(self.process.name, body)
        self.assertIn(self.team.name, body)
        self.assertIn('2 items to work through', body)
        self.assertIn('Walk-in customer, branch referral.', body)
        self.assertIn(f'https://portal.dash-mfb.com/app/tasks/{task.pk}/', body)
        self.assertIn('Press Start work', body)

    def test_assigning_to_yourself_sends_nothing(self):
        self.raise_task(self.staff, self.staff)
        self.assertEqual(len(mail.outbox), 0)

    def test_raising_it_unassigned_sends_nothing(self):
        task = self.raise_task(self.lead, None)
        self.assertIsNotNone(task)
        self.assertIsNone(task.assignee_id)
        self.assertEqual(len(mail.outbox), 0)

    def test_reassigning_emails_only_the_new_person(self):
        task = self.raise_task(self.lead, self.staff)
        mail.outbox = []

        self.client_for(self.lead).post(reverse('task-update', kwargs={'pk': task.pk}), {
            'title': task.title, 'process_type': self.process.pk,
            'team': self.team.pk, 'assignee': self.other.pk, 'notes': task.notes,
        })
        task.refresh_from_db()

        self.assertEqual(task.assignee_id, self.other.pk)
        self.assertEqual([m.to for m in mail.outbox], [[self.other.email]])

    def test_picking_up_an_unassigned_handover_emails_the_new_owner(self):
        task = self.raise_task(self.lead, None)
        mail.outbox = []

        self.client_for(self.lead).post(reverse('task-update', kwargs={'pk': task.pk}), {
            'title': task.title, 'process_type': self.process.pk,
            'team': self.team.pk, 'assignee': self.other.pk, 'notes': task.notes,
        })

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.other.email])

    def test_an_unrelated_edit_sends_nothing(self):
        task = self.raise_task(self.lead, self.staff)
        mail.outbox = []

        self.client_for(self.lead).post(reverse('task-update', kwargs={'pk': task.pk}), {
            'title': 'Renamed, same person', 'process_type': self.process.pk,
            'team': self.team.pk, 'assignee': self.staff.pk, 'notes': task.notes,
        })
        task.refresh_from_db()

        self.assertEqual(task.title, 'Renamed, same person')
        self.assertEqual(len(mail.outbox), 0, 'resaving the same assignee is not news')

    @override_settings(ASSIGNMENT_EMAILS_ENABLED=False)
    def test_the_switch_turns_it_off(self):
        self.raise_task(self.lead, self.staff)
        self.assertEqual(len(mail.outbox), 0)

    def test_a_broken_mail_server_does_not_undo_the_assignment(self):
        """The assignment is the real work; the email is a courtesy."""
        with override_settings(EMAIL_BACKEND='portal.does.not.Exist'):
            task = self.raise_task(self.lead, self.staff)

        self.assertIsNotNone(task)
        self.assertEqual(task.assignee_id, self.staff.pk)


@override_settings(SITE_URL='https://portal.dash-mfb.com')
class EmailFormatTests(TestCase):
    """Every email goes out as plain text plus HTML.

    The text part is not decoration: some clients refuse HTML, some people read
    mail in a terminal, and a message with no text alternative scores worse
    with spam filters.
    """

    def setUp(self):
        self.lead = make_user('fmt_lead', Profile.ROLE_TEAM_LEAD, 'Ada')
        self.staff = make_user('fmt_staff', Profile.ROLE_STAFF, 'Tunde')
        self.team = Team.objects.create(name='Format Team', lead=self.lead)
        for user in (self.lead, self.staff):
            user.profile.teams.add(self.team)
        self.process = ProcessType.objects.create(
            name='Format Process', target_hours=8, checklist=['One'],
            approval_level=ProcessType.APPROVAL_LEAD,
        )
        mail.outbox = []

    def client_for(self, user):
        client = self.client_class()
        self.assertTrue(client.login(username=user.email, password=PW))
        return client

    def make_task(self):
        return Task.objects.create(
            title='Onboard Chidera Nwosu', process_type=self.process,
            assignee=self.staff, team=self.team, created_by=self.lead,
            notes='Walk-in customer.',
        )

    def assert_well_formed(self, message):
        """Both parts present, and the HTML part actually is HTML."""
        self.assertTrue(message.body.strip(), 'the plain text part is empty')
        self.assertEqual(len(message.alternatives), 1, 'no HTML alternative attached')

        html, mimetype = message.alternatives[0]
        self.assertEqual(mimetype, 'text/html')
        self.assertIn('<!DOCTYPE html>', html)
        self.assertIn('Dash MFB', html)
        self.assertNotIn('{{', html)
        self.assertNotIn('{%', html)

        # Email clients block remote content and ignore linked stylesheets.
        self.assertNotIn('<img', html)
        self.assertNotIn('<link', html)
        self.assertNotIn('cdn.', html)
        return html

    def test_assignment_email_is_multipart(self):
        from . import notify
        notify.assigned(self.make_task(), self.lead)

        html = self.assert_well_formed(mail.outbox[0])
        self.assertIn('assigned to you', html.lower())
        self.assertIn('Walk-in customer.', html)

    def test_review_request_email_is_multipart(self):
        from . import notify
        task = self.make_task()
        task.checklist_done = {'0': True}
        task.save()
        approvals.submit(task)
        notify.submitted_for_review(task, self.staff)

        html = self.assert_well_formed(mail.outbox[0])
        self.assertIn('sign-off', html.lower())

    def test_returned_email_carries_the_reason_in_both_parts(self):
        from . import notify
        task = self.make_task()
        task.checklist_done = {'0': True}
        task.save()
        approvals.submit(task)
        reason = 'BVN does not match the ID document.'
        approvals.send_back(task, self.lead, reason)
        mail.outbox = []
        notify.decision_made(task, self.lead, Approval.DECISION_RETURNED, reason)

        message = mail.outbox[0]
        html = self.assert_well_formed(message)
        self.assertIn(reason, message.body)
        self.assertIn(reason, html)

    def test_reminder_and_digest_build_both_parts(self):
        from . import digests as digest_mod
        from . import reminders

        task = self.make_task()
        subject, text, html = reminders.build_email(task, reminders.FINAL)
        self.assertTrue(subject and text.strip())
        self.assertIn('<!DOCTYPE html>', html)
        self.assertNotIn('{{', html)

        task.checklist_done = {'0': True}
        task.save()
        approvals.submit(task)
        pending = digest_mod.waiting_on(self.lead)
        subject, text, html = digest_mod.build_email(self.lead, pending)
        self.assertTrue(subject and text.strip())
        self.assertIn('<!DOCTYPE html>', html)
        self.assertIn(task.title, html)
        self.assertNotIn('{{', html)

    def test_the_invite_is_multipart(self):
        from users.views import send_invite

        invited = User.objects.create_user(username='fmt_new@dash-mfb.com',
                                           email='fmt_new@dash-mfb.com',
                                           first_name='New', last_name='Starter')
        invited.set_unusable_password()
        invited.save()
        mail.outbox = []

        request = self.client_class().request().wsgi_request
        send_invite(request, invited)

        html = self.assert_well_formed(mail.outbox[0])
        self.assertIn('fmt_new@dash-mfb.com', html)


@override_settings(SITE_URL='https://portal.dash-mfb.com')
class SignedOffEmailTests(TestCase):
    """Whoever did the work is told when it is finally signed off.

    Explicitly asked for by the owners, so it is pinned here rather than left
    resting on the more general decision_made tests.
    """

    def setUp(self):
        self.head = make_user('so2_head', Profile.ROLE_DEPT_HEAD, 'Chika')
        self.lead = make_user('so2_lead', Profile.ROLE_TEAM_LEAD, 'Ada')
        self.staff = make_user('so2_staff', Profile.ROLE_STAFF, 'Tunde')
        self.team = Team.objects.create(name='Signed Off Team', lead=self.lead)
        for user in (self.lead, self.staff):
            user.profile.teams.add(self.team)

    def client_for(self, user):
        client = self.client_class()
        self.assertTrue(client.login(username=user.email, password=PW))
        return client

    def submitted_task(self, approval_level):
        process = ProcessType.objects.create(
            name=f'Signed Off {approval_level}', target_hours=8,
            checklist=['One'], approval_level=approval_level,
        )
        task = Task.objects.create(title='Onboard Chidera Nwosu', process_type=process,
                                   assignee=self.staff, team=self.team,
                                   created_by=self.staff)
        task.checklist_done = {'0': True}
        task.save()
        approvals.submit(task)
        task.refresh_from_db()
        return task

    def approve(self, user, task, comment=''):
        self.client_for(user).post(
            reverse('task-approve', kwargs={'pk': task.pk}), {'comment': comment})
        task.refresh_from_db()

    def signed_off_email(self):
        for message in mail.outbox:
            if message.to == [self.staff.email] and 'Signed off' in message.subject:
                return message
        return None

    def test_one_stage_sign_off_emails_the_person_who_did_it(self):
        task = self.submitted_task(ProcessType.APPROVAL_LEAD)
        mail.outbox = []

        self.approve(self.lead, task, 'Verified against the register.')

        self.assertEqual(task.approval_stage, Task.STAGE_APPROVED)
        self.assertIsNotNone(task.completed_at)

        message = self.signed_off_email()
        self.assertIsNotNone(message, 'nothing told the assignee it was signed off')
        self.assertIn('Onboard Chidera Nwosu', message.subject)
        self.assertIn('nothing further', message.body)
        self.assertIn('Verified against the register.', message.body)

        html = message.alternatives[0][0]
        self.assertIn('Signed off', html)
        self.assertIn('Complete', html)

    def test_two_stage_sign_off_tells_them_at_each_step(self):
        task = self.submitted_task(ProcessType.APPROVAL_LEAD_HEAD)

        mail.outbox = []
        self.approve(self.lead, task)
        self.assertEqual(task.approval_stage, Task.STAGE_HEAD_REVIEW)
        self.assertIsNone(self.signed_off_email(), 'not finished yet, so not signed off')
        interim = [m for m in mail.outbox if m.to == [self.staff.email]]
        self.assertEqual(len(interim), 1)
        self.assertIn('Department Head', interim[0].subject)

        mail.outbox = []
        self.approve(self.head, task)
        self.assertEqual(task.approval_stage, Task.STAGE_APPROVED)
        self.assertIsNotNone(self.signed_off_email())

    def test_the_sign_off_email_reports_both_clocks(self):
        task = self.submitted_task(ProcessType.APPROVAL_LEAD)
        mail.outbox = []
        self.approve(self.lead, task)

        html = self.signed_off_email().alternatives[0][0]
        self.assertIn('Your time', html)
        self.assertIn('Time in review', html)

    def test_a_manager_signing_off_their_own_work_is_not_emailed(self):
        process = ProcessType.objects.create(
            name='Signed Off Head Own', target_hours=8, checklist=['One'],
            approval_level=ProcessType.APPROVAL_LEAD,
        )
        task = Task.objects.create(title='Head own work', process_type=process,
                                   assignee=self.lead, team=self.team,
                                   created_by=self.lead)
        task.checklist_done = {'0': True}
        task.save()
        approvals.submit(task)
        task.refresh_from_db()

        mail.outbox = []
        self.approve(self.head, task)

        self.assertEqual(task.approval_stage, Task.STAGE_APPROVED)
        told = [m for m in mail.outbox if m.to == [self.lead.email]]
        self.assertEqual(len(told), 1, 'the lead did the work, so the lead is told')
        self.assertIn('Signed off', told[0].subject)


@override_settings(SITE_URL='https://portal.dash-mfb.com')
class HandoverNotificationTests(TestCase):
    """Work sent to another team arrives unowned, so its lead has to be told.

    Without this the handover is silent: nobody is assigned, so the assignment
    email has no recipient, and the receiving lead only finds out from the
    digest hours later while the deadline runs.
    """

    def setUp(self):
        self.head = make_user('hv_head', Profile.ROLE_DEPT_HEAD, 'Chika')
        self.ada = make_user('hv_ada', Profile.ROLE_TEAM_LEAD, 'Ada')
        self.ibrahim = make_user('hv_ib', Profile.ROLE_TEAM_LEAD, 'Ibrahim')
        self.tunde = make_user('hv_tunde', Profile.ROLE_STAFF, 'Tunde')
        self.segun = make_user('hv_segun', Profile.ROLE_STAFF, 'Segun')

        self.accounts = Team.objects.create(name='Handover Accounts', lead=self.ada)
        self.payments = Team.objects.create(name='Handover Payments', lead=self.ibrahim)
        for user, team in [(self.ada, self.accounts), (self.tunde, self.accounts),
                           (self.ibrahim, self.payments), (self.segun, self.payments)]:
            user.profile.teams.add(team)

        self.process = ProcessType.objects.create(
            name='Handover Process', target_hours=8, checklist=['One'],
            approval_level=ProcessType.APPROVAL_LEAD,
        )
        mail.outbox = []

    def client_for(self, user):
        client = self.client_class()
        self.assertTrue(client.login(username=user.email, password=PW))
        return client

    def raise_task(self, actor, team, assignee=None, title='Set up standing order'):
        data = {'title': title, 'process_type': self.process.pk,
                'team': team.pk, 'notes': 'Account opened this morning.'}
        if assignee:
            data['assignee'] = assignee.pk
        self.client_for(actor).post(reverse('task-create'), data)
        return Task.objects.filter(title=title).first()

    def recipients(self):
        return sorted(address for m in mail.outbox for address in m.to)

    def test_sending_work_to_another_team_tells_its_lead(self):
        task = self.raise_task(self.tunde, self.payments)

        self.assertIsNone(task.assignee_id, 'handovers arrive unowned by design')
        self.assertEqual(self.recipients(), [self.ibrahim.email])

        message = mail.outbox[0]
        self.assertIn('Needs an owner', message.subject)
        self.assertIn(self.payments.name, message.subject)
        self.assertIn('Tunde Test', message.body)
        self.assertIn('Account opened this morning.', message.body)

        html = message.alternatives[0][0]
        self.assertIn('Nobody yet', html)
        self.assertIn('Assign somebody', html)

    def test_the_sender_lead_is_not_told_about_their_own_handover(self):
        self.raise_task(self.ibrahim, self.payments)
        self.assertEqual(self.recipients(), [],
                         'the lead sent it to their own team, so they know')

    def test_the_head_raising_unowned_work_tells_that_teams_lead(self):
        self.raise_task(self.head, self.accounts)
        self.assertEqual(self.recipients(), [self.ada.email])

    def test_assigning_it_emails_the_new_owner_not_the_lead_again(self):
        task = self.raise_task(self.tunde, self.payments)
        mail.outbox = []

        self.client_for(self.ibrahim).post(
            reverse('task-update', kwargs={'pk': task.pk}), {
                'title': task.title, 'process_type': self.process.pk,
                'team': self.payments.pk, 'assignee': self.segun.pk,
                'notes': task.notes,
            })
        task.refresh_from_db()

        self.assertEqual(task.assignee_id, self.segun.pk)
        self.assertEqual(self.recipients(), [self.segun.email])

    def test_moving_an_owned_task_to_another_team_tells_the_new_lead(self):
        task = self.raise_task(self.head, self.accounts, assignee=self.tunde)
        mail.outbox = []

        self.client_for(self.head).post(
            reverse('task-update', kwargs={'pk': task.pk}), {
                'title': task.title, 'process_type': self.process.pk,
                'team': self.payments.pk, 'notes': task.notes,
            })
        task.refresh_from_db()

        self.assertEqual(task.team_id, self.payments.pk)
        self.assertIsNone(task.assignee_id)
        self.assertEqual(self.recipients(), [self.ibrahim.email])

    def test_an_unrelated_edit_does_not_re_notify(self):
        task = self.raise_task(self.tunde, self.payments)
        mail.outbox = []

        self.client_for(self.ibrahim).post(
            reverse('task-update', kwargs={'pk': task.pk}), {
                'title': 'Renamed, still nobody on it',
                'process_type': self.process.pk,
                'team': self.payments.pk, 'notes': task.notes,
            })
        task.refresh_from_db()

        self.assertEqual(task.title, 'Renamed, still nobody on it')
        self.assertEqual(self.recipients(), [], 'the lead was already told once')

    @override_settings(ASSIGNMENT_EMAILS_ENABLED=False)
    def test_the_switch_covers_handovers_too(self):
        self.raise_task(self.tunde, self.payments)
        self.assertEqual(self.recipients(), [])


@override_settings(SITE_URL='https://portal.dash-mfb.com', REMINDER_HOURS=(0, 0))
class ReminderCronTests(TestCase):
    """The reminder command, run the way cron runs it: repeatedly.

    Every one of these came from running the command twice rather than trusting
    the rules in isolation. A task used to get its final warning and then a
    routine reminder on the very next run, minutes apart, saying the same thing.
    """

    def setUp(self):
        self.lead = make_user('cron_lead', Profile.ROLE_TEAM_LEAD, 'Ada')
        self.staff = make_user('cron_staff', Profile.ROLE_STAFF, 'Tunde')
        self.team = Team.objects.create(name='Cron Team', lead=self.lead)
        for user in (self.lead, self.staff):
            user.profile.teams.add(self.team)
        self.process = ProcessType.objects.create(
            name='Cron Process', target_hours=10, checklist=['One'],
            approval_level=ProcessType.APPROVAL_LEAD,
        )
        self.now = timezone.now()
        mail.outbox = []

    def make_task(self, title, created_hours_ago, deadline_in_hours):
        task = Task.objects.create(title=title, process_type=self.process,
                                   assignee=self.staff, team=self.team,
                                   created_by=self.staff)
        Task.objects.filter(pk=task.pk).update(
            created_at=self.now - timedelta(hours=created_hours_ago),
            deadline=self.now + timedelta(hours=deadline_in_hours),
        )
        return Task.objects.get(pk=task.pk)

    def run_cron(self):
        mail.outbox = []
        call_command('send_task_reminders', verbosity=0)
        return list(mail.outbox)

    def test_each_kind_fires_once_and_then_stops(self):
        self.make_task('Halfway through', 6, 4)
        self.make_task('Almost out of time', 9.9, 0.2)
        self.make_task('Already overdue', 30, -26)

        first = self.run_cron()
        self.assertEqual(len(first), 3, [m.subject for m in first])

        for _ in range(3):
            self.assertEqual(self.run_cron(), [],
                             'cron runs every few minutes, so repeats must send nothing')

    def test_a_final_warning_is_not_followed_by_a_routine_reminder(self):
        task = self.make_task('Almost out of time', 9.9, 0.2)

        sent = self.run_cron()
        self.assertEqual(len(sent), 1)
        self.assertIn('minutes left', sent[0].subject)

        task.refresh_from_db()
        self.assertIsNotNone(task.final_warning_at)
        self.assertIsNone(reminders.due_kind(task, self.now + timedelta(minutes=5)),
                          'the final warning is the last word before the deadline')

    def test_an_overdue_task_is_chased_daily_not_immediately(self):
        task = self.make_task('Already overdue', 30, -26)
        self.run_cron()
        task.refresh_from_db()

        self.assertIsNone(reminders.due_kind(task, self.now + timedelta(hours=23)))
        self.assertEqual(reminders.due_kind(task, self.now + timedelta(hours=25)),
                         reminders.REMINDER)

    def test_the_command_sends_both_parts(self):
        self.make_task('Halfway through', 6, 4)
        sent = self.run_cron()

        message = sent[0]
        self.assertTrue(message.body.strip())
        self.assertEqual(len(message.alternatives), 1)
        html = message.alternatives[0][0]
        self.assertIn('<!DOCTYPE html>', html)
        self.assertIn('https://portal.dash-mfb.com', html)
        self.assertNotIn('{{', html)

    def test_dry_run_changes_nothing(self):
        task = self.make_task('Halfway through', 6, 4)
        mail.outbox = []
        call_command('send_task_reminders', '--dry-run', verbosity=0)

        task.refresh_from_db()
        self.assertEqual(mail.outbox, [])
        self.assertIsNone(task.reminder_sent_at)
        self.assertEqual(task.reminders_sent, 0)


class AuditorAccessTests(TestCase):
    """The Auditor reads everything and writes nothing.

    Read-only is easy to believe and hard to guarantee: several write views
    ask only "can you see this task?", which an auditor can. Each of those is
    pinned here.
    """

    def setUp(self):
        self.admin = make_user('aud_admin', Profile.ROLE_ADMIN, 'Bola')
        self.head = make_user('aud_head', Profile.ROLE_DEPT_HEAD, 'Chika')
        self.lead = make_user('aud_lead', Profile.ROLE_TEAM_LEAD, 'Ada')
        self.staff = make_user('aud_staff', Profile.ROLE_STAFF, 'Tunde')
        self.auditor = make_user('aud_auditor', Profile.ROLE_AUDITOR, 'Ife')

        self.team = Team.objects.create(name='Audit Team', lead=self.lead)
        for user in (self.lead, self.staff):
            user.profile.teams.add(self.team)

        self.process = ProcessType.objects.create(
            name='Audit Process', target_hours=8, checklist=['Only item'],
        )
        # Nothing links the auditor to this task: not the assignee, not the
        # team, not the person who raised it.
        self.task = Task.objects.create(
            title='Someone else work', process_type=self.process,
            assignee=self.staff, team=self.team, created_by=self.lead,
        )

    def client_for(self, user):
        client = self.client_class()
        self.assertTrue(client.login(username=user.email, password=PW))
        return client

    # --- what they may read -----------------------------------------------

    def test_auditor_sees_the_day_report(self):
        response = self.client_for(self.auditor).get(reverse('portal-day'))
        self.assertEqual(response.status_code, 200)

    def test_auditor_sees_analytics(self):
        response = self.client_for(self.auditor).get(reverse('portal-analytics'))
        self.assertEqual(response.status_code, 200)

    def test_auditor_reads_a_task_they_have_nothing_to_do_with(self):
        response = self.client_for(self.auditor).get(
            reverse('task-detail', args=[self.task.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Someone else work')

    def test_auditor_lands_on_the_day_report(self):
        response = self.client_for(self.auditor).get(reverse('portal-home'))
        self.assertRedirects(response, reverse('portal-day'))

    def test_the_task_page_offers_an_auditor_nothing_to_click(self):
        response = self.client_for(self.auditor).get(
            reverse('task-detail', args=[self.task.pk])
        )
        self.assertTrue(response.context['read_only'])
        for flag in ('can_submit', 'can_review', 'can_edit', 'can_start'):
            self.assertFalse(response.context[flag], flag)
        self.assertNotContains(response, reverse('comment-create', args=[self.task.pk]))
        self.assertNotContains(response, reverse('attachment-upload', args=[self.task.pk]))

    # --- what they may not do ---------------------------------------------

    def test_auditor_cannot_comment(self):
        response = self.client_for(self.auditor).post(
            reverse('comment-create', args=[self.task.pk]), {'body': 'Looks wrong'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.task.comments.count(), 0)

    def test_auditor_cannot_upload(self):
        response = self.client_for(self.auditor).post(
            reverse('attachment-upload', args=[self.task.pk]), {},
        )
        self.assertEqual(response.status_code, 403)

    def test_auditor_cannot_raise_a_task(self):
        response = self.client_for(self.auditor).post(reverse('task-create'), {
            'title': 'Mine now', 'process_type': self.process.pk, 'team': self.team.pk,
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Task.objects.filter(title='Mine now').exists())

    def test_auditor_cannot_approve(self):
        self.task.approval_stage = Task.STAGE_LEAD_REVIEW
        self.task.save(update_fields=['approval_stage'])

        # This view refuses politely rather than with a 403; what matters is
        # that the stage does not move and no sign-off is recorded.
        self.client_for(self.auditor).post(
            reverse('task-approve', args=[self.task.pk]), {},
        )
        self.task.refresh_from_db()
        self.assertEqual(self.task.approval_stage, Task.STAGE_LEAD_REVIEW)
        self.assertEqual(self.task.approvals.count(), 0)

    def test_auditor_cannot_archive(self):
        response = self.client_for(self.auditor).post(
            reverse('task-archive', args=[self.task.pk]), {},
        )
        self.assertEqual(response.status_code, 403)
        self.task.refresh_from_db()
        self.assertIsNone(self.task.archived_at)

    def test_auditor_is_kept_out_of_the_management_pages(self):
        client = self.client_for(self.auditor)
        for name in ('portal-staff', 'portal-teams'):
            response = client.get(reverse(name))
            self.assertRedirects(response, reverse('portal-home'),
                                 target_status_code=302, msg_prefix=name)

    def test_auditor_has_no_queues(self):
        self.assertEqual(queues.visible_queues(self.auditor), [])
        response = self.client_for(self.auditor).get(reverse('queue', args=['my-work']))
        self.assertEqual(response.status_code, 403)

    def test_auditor_is_not_management(self):
        self.assertFalse(queues.is_management(self.auditor))
        self.assertFalse(queues.is_head(self.auditor))
        self.assertTrue(queues.can_report(self.auditor))

    def test_auditor_cannot_be_given_work(self):
        from .forms import TaskForm
        assignable = TaskForm(user=self.lead).fields['assignee'].queryset
        self.assertIn(self.staff, assignable)
        self.assertNotIn(self.auditor, assignable)

    # --- and the reporting pages stay shut to everyone below a lead -------

    def test_management_still_sees_the_day_report(self):
        for user in (self.head, self.lead, self.admin):
            response = self.client_for(user).get(reverse('portal-day'))
            self.assertEqual(response.status_code, 200, user.email)

    def test_staff_cannot_see_the_day_report(self):
        response = self.client_for(self.staff).get(reverse('portal-day'))
        self.assertRedirects(response, reverse('portal-home'), target_status_code=302)


class AttachmentAccessLogTests(TestCase):
    """Every document read leaves a row behind."""

    def setUp(self):
        self.lead = make_user('log_lead', Profile.ROLE_TEAM_LEAD, 'Ada')
        self.staff = make_user('log_staff', Profile.ROLE_STAFF, 'Tunde')
        self.auditor = make_user('log_auditor', Profile.ROLE_AUDITOR, 'Ife')
        self.team = Team.objects.create(name='Log Team', lead=self.lead)
        self.process = ProcessType.objects.create(name='Log Process', target_hours=4)
        self.task = Task.objects.create(
            title='With a document', process_type=self.process,
            assignee=self.staff, team=self.team, created_by=self.lead,
        )
        self.attachment = Attachment.objects.create(
            task=self.task,
            file=SimpleUploadedFile('statement.pdf', b'%PDF-1.4 pretend'),
            original_name='statement.pdf', size=16, uploaded_by=self.staff,
        )

    def tearDown(self):
        self.attachment.file.delete(save=False)

    def client_for(self, user):
        client = self.client_class()
        self.assertTrue(client.login(username=user.email, password=PW))
        return client

    def test_opening_a_document_is_recorded(self):
        response = self.client_for(self.auditor).get(
            reverse('attachment-download', args=[self.attachment.pk])
        )
        self.assertEqual(response.status_code, 200)
        response.close()

        entry = AttachmentAccess.objects.get()
        self.assertEqual(entry.user, self.auditor)
        self.assertEqual(entry.task, self.task)
        self.assertEqual(entry.file_name, 'statement.pdf')

    def test_a_refused_download_records_nothing(self):
        outsider = make_user('log_outsider', Profile.ROLE_STAFF, 'Zainab')
        response = self.client_for(outsider).get(
            reverse('attachment-download', args=[self.attachment.pk])
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(AttachmentAccess.objects.count(), 0)

    def test_the_trail_outlives_the_document(self):
        client = self.client_for(self.staff)
        client.get(reverse('attachment-download', args=[self.attachment.pk])).close()

        client.post(reverse('attachment-delete', args=[self.attachment.pk]))

        entry = AttachmentAccess.objects.get()
        self.assertIsNone(entry.attachment)
        self.assertEqual(entry.file_name, 'statement.pdf')
        self.assertEqual(entry.user, self.staff)


class DayReportTests(TestCase):
    """What was completed, on which day, in Lagos time."""

    def setUp(self):
        self.head = make_user('day_head', Profile.ROLE_DEPT_HEAD, 'Chika')
        self.lead = make_user('day_lead', Profile.ROLE_TEAM_LEAD, 'Ada')
        self.staff = make_user('day_staff', Profile.ROLE_STAFF, 'Tunde')
        self.auditor = make_user('day_auditor', Profile.ROLE_AUDITOR, 'Ife')
        self.team = Team.objects.create(name='Day Team', lead=self.lead)
        self.process = ProcessType.objects.create(name='Day Process', target_hours=8)

    def client_for(self, user):
        client = self.client_class()
        self.assertTrue(client.login(username=user.email, password=PW))
        return client

    def make_completed(self, title, completed_at, submitted_at=None):
        task = Task.objects.create(
            title=title, process_type=self.process,
            assignee=self.staff, team=self.team, created_by=self.lead,
        )
        Task.objects.filter(pk=task.pk).update(
            approval_stage=Task.STAGE_APPROVED,
            submitted_at=submitted_at or completed_at - timedelta(hours=1),
            completed_at=completed_at,
        )
        return Task.objects.get(pk=task.pk)

    def titles_on(self, **params):
        response = self.client_for(self.auditor).get(reverse('portal-day'), params)
        self.assertEqual(response.status_code, 200)
        return [row['task'].title for row in response.context['rows']]

    def test_today_shows_only_todays_completions(self):
        now = timezone.now()
        self.make_completed('Finished today', now - timedelta(minutes=5))
        self.make_completed('Finished last week', now - timedelta(days=7))

        self.assertEqual(self.titles_on(), ['Finished today'])

    def test_last_seven_days_reaches_back(self):
        now = timezone.now()
        self.make_completed('Finished today', now - timedelta(minutes=5))
        self.make_completed('Finished three days ago', now - timedelta(days=3))
        self.make_completed('Finished last month', now - timedelta(days=31))

        titles = self.titles_on(preset='week')
        self.assertIn('Finished today', titles)
        self.assertIn('Finished three days ago', titles)
        self.assertNotIn('Finished last month', titles)

    def test_a_day_means_a_lagos_day_not_a_utc_one(self):
        """00:30 in Lagos is still the previous day in UTC.

        Filtering on UTC days would file that task under yesterday, and the
        list an auditor checks against Ncube's would be short by an hour of
        every night.
        """
        lagos = ZoneInfo('Africa/Lagos')
        local_midnight_ish = datetime(2026, 6, 15, 0, 30, tzinfo=lagos)
        self.make_completed('Just after midnight', local_midnight_ish)

        self.assertEqual(self.titles_on(**{'from': '2026-06-15', 'to': '2026-06-15'}),
                         ['Just after midnight'])
        self.assertEqual(self.titles_on(**{'from': '2026-06-14', 'to': '2026-06-14'}), [])

    def test_handed_in_but_unsigned_is_counted_not_listed(self):
        task = Task.objects.create(
            title='Waiting on the lead', process_type=self.process,
            assignee=self.staff, team=self.team, created_by=self.lead,
        )
        Task.objects.filter(pk=task.pk).update(
            approval_stage=Task.STAGE_LEAD_REVIEW, submitted_at=timezone.now(),
        )

        response = self.client_for(self.auditor).get(reverse('portal-day'))
        self.assertEqual(response.context['pending_count'], 1)
        self.assertEqual(list(response.context['rows']), [])

    def test_the_report_names_who_signed_it_off(self):
        task = self.make_completed('Signed off', timezone.now() - timedelta(minutes=5))
        Approval.objects.create(
            task=task, actor=self.lead, stage=Approval.STAGE_LEAD,
            decision=Approval.DECISION_APPROVED,
        )

        response = self.client_for(self.auditor).get(reverse('portal-day'))
        self.assertEqual(response.context['rows'][0]['signed_off_by'], self.lead)

    def test_counts_split_on_time_from_late(self):
        now = timezone.now()
        early = self.make_completed('Beat the clock', now - timedelta(minutes=5))
        late = self.make_completed('Missed it', now - timedelta(minutes=5))
        Task.objects.filter(pk=late.pk).update(deadline=now - timedelta(days=1))

        response = self.client_for(self.auditor).get(reverse('portal-day'))
        self.assertEqual(response.context['completed_count'], 2)
        self.assertEqual(response.context['late_count'], 1)
        self.assertEqual(response.context['on_time_count'], 1)
        self.assertEqual(response.context['people_count'], 1)
        self.assertIsNotNone(early.completed_at)

    # --- the export --------------------------------------------------------

    def workbook_for(self, **params):
        response = self.client_for(self.auditor).get(
            reverse('portal-day'), {'export': 'xlsx', **params},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('spreadsheetml', response['Content-Type'])
        self.assertIn('.xlsx', response['Content-Disposition'])
        return load_workbook(io.BytesIO(response.content)).active

    def column_values(self, sheet, heading):
        headings = [cell.value for cell in sheet[1]]
        index = headings.index(heading)
        return [row[index] for row in sheet.iter_rows(min_row=2, values_only=True)]

    def test_export_is_a_workbook_of_the_same_day(self):
        self.make_completed('Exported task', timezone.now() - timedelta(minutes=5))
        self.make_completed('Old task', timezone.now() - timedelta(days=9))

        sheet = self.workbook_for()
        self.assertEqual(self.column_values(sheet, 'Task'), ['Exported task'])
        self.assertIn('Signed off by', [cell.value for cell in sheet[1]])

    def test_dates_and_hours_arrive_as_dates_and_numbers(self):
        """The reason for a workbook rather than a CSV.

        Read from a CSV, every one of these is text: the columns cannot be
        sorted, and Excel shows ##### until each is widened by hand.
        """
        self.make_completed('Typed properly', timezone.now() - timedelta(minutes=5))
        sheet = self.workbook_for()

        completed = sheet.cell(row=2, column=1)
        self.assertIsInstance(completed.value, datetime)
        self.assertEqual(completed.number_format, 'yyyy-mm-dd hh:mm')
        # Excel holds no timezone, so the value is local wall-clock time.
        self.assertIsNone(completed.value.tzinfo)

        total = self.column_values(sheet, 'Total hrs')[0]
        self.assertIsInstance(total, (int, float))

    def test_every_column_is_wide_enough_to_show_its_contents(self):
        sheet = self.workbook_for()
        for index in range(1, len(sheet[1]) + 1):
            width = sheet.column_dimensions[get_column_letter(index)].width
            self.assertTrue(width and width >= 7,
                            f'column {index} has no usable width')

    def test_export_carries_document_names_but_never_documents(self):
        task = self.make_completed('With papers', timezone.now() - timedelta(minutes=5))
        attachment = Attachment.objects.create(
            task=task, file=SimpleUploadedFile('kyc.pdf', b'%PDF-1.4 pretend'),
            original_name='kyc.pdf', size=16, uploaded_by=self.staff,
        )
        self.addCleanup(attachment.file.delete, save=False)

        response = self.client_for(self.auditor).get(
            reverse('portal-day'), {'export': 'xlsx'},
        )
        sheet = load_workbook(io.BytesIO(response.content)).active

        self.assertEqual(self.column_values(sheet, 'File names'), ['kyc.pdf'])
        self.assertEqual(self.column_values(sheet, 'Files'), [1])
        self.assertNotIn(b'%PDF-1.4 pretend', response.content)
        self.assertIn(reverse('task-detail', args=[task.pk]),
                      self.column_values(sheet, 'Link')[0])

    def test_export_will_not_hand_excel_a_formula(self):
        self.make_completed('=cmd|/c calc', timezone.now() - timedelta(minutes=5))
        sheet = self.workbook_for()

        title = sheet.cell(row=2, column=2)
        self.assertEqual(title.value, '=cmd|/c calc')
        self.assertEqual(title.data_type, 's', 'stored as a formula, not text')

    # --- and the range is bounded -----------------------------------------

    def test_a_silly_range_is_capped_rather_than_scanning_everything(self):
        response = self.client_for(self.auditor).get(
            reverse('portal-day'), {'from': '1990-01-01', 'to': '2026-01-01'},
        )
        window = response.context['window']
        self.assertTrue(window['capped'])
        self.assertEqual((window['end'] - window['start']).days, 366)

    def test_a_backwards_range_is_read_the_right_way_round(self):
        response = self.client_for(self.auditor).get(
            reverse('portal-day'), {'from': '2026-06-30', 'to': '2026-06-01'},
        )
        window = response.context['window']
        self.assertEqual(str(window['start']), '2026-06-01')
        self.assertEqual(str(window['end']), '2026-06-30')

    def test_nonsense_dates_fall_back_to_today(self):
        response = self.client_for(self.auditor).get(
            reverse('portal-day'), {'from': 'yesterday-ish', 'to': ''},
        )
        self.assertEqual(response.context['window']['start'], timezone.localdate())


class UploadRulesTests(TestCase):
    """What may be attached, and how it comes back out."""

    EMAIL = (
        b'From: customer@example.com\r\n'
        b'To: ops@dash-mfb.com\r\n'
        b'Subject: Standing instruction\r\n\r\n'
        b'Please action the transfer.\r\n'
    )

    def setUp(self):
        self.lead = make_user('up_lead', Profile.ROLE_TEAM_LEAD, 'Ada')
        self.staff = make_user('up_staff', Profile.ROLE_STAFF, 'Tunde')
        self.team = Team.objects.create(name='Upload Team', lead=self.lead)
        self.process = ProcessType.objects.create(name='Upload Process', target_hours=4)
        self.task = Task.objects.create(
            title='Needs evidence', process_type=self.process,
            assignee=self.staff, team=self.team, created_by=self.lead,
        )
        self.client_ = self.client_class()
        self.assertTrue(self.client_.login(username=self.staff.email, password=PW))

    def upload(self, name, content=b'x'):
        response = self.client_.post(
            reverse('attachment-upload', args=[self.task.pk]),
            {'files': SimpleUploadedFile(name, content)},
        )
        for attachment in self.task.attachments.all():
            self.addCleanup(attachment.file.delete, save=False)
        return response

    def test_a_saved_email_can_be_attached(self):
        self.upload('instruction.eml', self.EMAIL)

        attachment = self.task.attachments.get()
        self.assertEqual(attachment.original_name, 'instruction.eml')
        self.assertFalse(attachment.is_image)

    def test_a_saved_email_is_handed_back_as_a_download(self):
        """Never inline. A .eml is markup a browser would happily render."""
        self.upload('instruction.eml', self.EMAIL)
        attachment = self.task.attachments.get()

        response = self.client_.get(reverse('attachment-download', args=[attachment.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment;', response['Content-Disposition'])
        response.close()

    def test_the_allowlist_refuses_everything_it_does_not_name(self):
        for name in ('payload.exe', 'page.html', 'shell.php', 'chart.svg', 'notes.eml.exe'):
            with self.subTest(name=name):
                self.upload(name)
                self.assertEqual(self.task.attachments.count(), 0, name)

    def test_a_file_over_the_limit_is_refused(self):
        oversized = b'x' * (settings.MAX_UPLOAD_BYTES + 1)
        self.upload('huge.pdf', oversized)
        self.assertEqual(self.task.attachments.count(), 0)

    def test_the_stored_name_cannot_be_chosen_by_the_uploader(self):
        """Two separate defences, and this pins both.

        Django strips the directory part of an uploaded name, so the label we
        keep is already harmless; and the name on disk is a fresh uuid, so
        nothing is reachable by guessing a URL either way.
        """
        self.upload('../../etc/passwd.txt', b'not really')

        attachment = self.task.attachments.get()
        self.assertEqual(attachment.original_name, 'passwd.txt')
        self.assertNotIn('passwd', attachment.file.name)
        self.assertNotIn('..', attachment.file.name)
        self.assertTrue(attachment.file.name.endswith('.txt'))


class ListOrderTests(TestCase):
    """Finished work reads newest first. Open work reads most urgent first.

    These sit here because the two rules pull in opposite directions and the
    completed queue quietly got the wrong one: every finished task scores the
    same on urgency, so the list fell back to deadline order and the newest
    sign-offs ended up on the last page.
    """

    def setUp(self):
        self.head = make_user('ord_head', Profile.ROLE_DEPT_HEAD, 'Chika')
        self.lead = make_user('ord_lead', Profile.ROLE_TEAM_LEAD, 'Ada')
        self.staff = make_user('ord_staff', Profile.ROLE_STAFF, 'Tunde')
        self.team = Team.objects.create(name='Order Team', lead=self.lead)
        self.staff.profile.teams.add(self.team)
        self.process = ProcessType.objects.create(name='Order Process', target_hours=8)

    def client_for(self, user):
        client = self.client_class()
        self.assertTrue(client.login(username=user.email, password=PW))
        return client

    def make(self, title, **fields):
        task = Task.objects.create(
            title=title, process_type=self.process,
            assignee=self.staff, team=self.team, created_by=self.lead,
        )
        if fields:
            Task.objects.filter(pk=task.pk).update(**fields)
        return Task.objects.get(pk=task.pk)

    def titles_in(self, user, key):
        response = self.client_for(user).get(reverse('queue', args=[key]))
        self.assertEqual(response.status_code, 200)
        return [task.title for task in response.context['tasks']]

    def test_completed_shows_the_newest_sign_off_first(self):
        now = timezone.now()
        for title, ago in (('Oldest', 30), ('Middle', 10), ('Newest', 1)):
            self.make(title, approval_stage=Task.STAGE_APPROVED,
                      submitted_at=now - timedelta(days=ago, hours=1),
                      completed_at=now - timedelta(days=ago))

        self.assertEqual(self.titles_in(self.head, 'completed'),
                         ['Newest', 'Middle', 'Oldest'])

    def test_completed_ignores_the_deadline_it_used_to_sort_by(self):
        """The old order. A task finished today but due long ago led the list."""
        now = timezone.now()
        self.make('Finished today, was due last year',
                  approval_stage=Task.STAGE_APPROVED,
                  completed_at=now, deadline=now - timedelta(days=365))
        self.make('Finished last month, due next year',
                  approval_stage=Task.STAGE_APPROVED,
                  completed_at=now - timedelta(days=30),
                  deadline=now + timedelta(days=365))

        self.assertEqual(self.titles_in(self.head, 'completed')[0],
                         'Finished today, was due last year')

    def test_an_approved_task_with_no_completion_time_does_not_squat_at_the_top(self):
        now = timezone.now()
        self.make('Recorded properly', approval_stage=Task.STAGE_APPROVED,
                  completed_at=now)
        self.make('Legacy row', approval_stage=Task.STAGE_APPROVED,
                  completed_at=None)

        self.assertEqual(self.titles_in(self.head, 'completed'),
                         ['Recorded properly', 'Legacy row'])

    def test_archived_shows_the_most_recently_archived_first(self):
        now = timezone.now()
        self.make('Archived first', archived_at=now - timedelta(days=5))
        self.make('Archived last', archived_at=now - timedelta(hours=1))

        self.assertEqual(self.titles_in(self.head, 'archived'),
                         ['Archived last', 'Archived first'])

    def test_open_work_still_leads_with_the_most_urgent(self):
        now = timezone.now()
        self.make('Due next week', deadline=now + timedelta(days=7))
        self.make('Overdue', deadline=now - timedelta(days=2))

        self.assertEqual(self.titles_in(self.staff, 'my-work')[0], 'Overdue')

    def test_the_day_report_leads_with_the_latest_sign_off(self):
        now = timezone.now()
        for title, minutes in (('Signed off at nine', 300), ('Signed off at noon', 60)):
            self.make(title, approval_stage=Task.STAGE_APPROVED,
                      submitted_at=now - timedelta(minutes=minutes + 30),
                      completed_at=now - timedelta(minutes=minutes))

        response = self.client_for(self.head).get(reverse('portal-day'))
        self.assertEqual([row['task'].title for row in response.context['rows']],
                         ['Signed off at noon', 'Signed off at nine'])
