"""Tests for the portal app.

Run with:  python manage.py test portal
"""
from datetime import timedelta

from django.core import mail
from django.core.management import call_command
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from users.models import Profile, Team

from . import approvals, digests, queues, reminders
from .forms import ProcessTypeForm
from .models import Approval, ProcessType, Task

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
