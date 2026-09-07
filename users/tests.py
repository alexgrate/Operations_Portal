"""Tests for the users app.

Run with:  python manage.py test users
"""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Profile, Team, assignable_roles, can_manage

PW = 'Testing!2345'


def make_user(slug, role, first):
    email = f'{slug}@dash-mfb.com'
    user = User.objects.create_user(username=email, email=email, password=PW,
                                    first_name=first, last_name='Test')
    user.profile.role = role
    user.profile.save()
    return user


class StaffHierarchyTests(TestCase):
    """Nobody manages sideways or upward.

    These exist because the staff endpoints originally checked only
    "team lead or above": any team lead could deactivate the department
    head, or promote themselves to Admin through the edit form.
    """

    def setUp(self):
        self.admin = make_user('h_admin', Profile.ROLE_ADMIN, 'Bola')
        self.head = make_user('h_head', Profile.ROLE_DEPT_HEAD, 'Chika')
        self.lead = make_user('h_lead', Profile.ROLE_TEAM_LEAD, 'Ada')
        self.staff = make_user('h_staff', Profile.ROLE_STAFF, 'Tunde')

    def client_for(self, user):
        client = self.client_class()
        self.assertTrue(client.login(username=user.email, password=PW))
        return client

    def toggle(self, actor, target):
        return self.client_for(actor).post(reverse('staff-toggle', args=[target.pk]))

    def assert_active(self, user, expected):
        user.refresh_from_db()
        self.assertIs(user.is_active, expected)

    # --- deactivation ------------------------------------------------------

    def test_team_lead_cannot_deactivate_department_head(self):
        self.toggle(self.lead, self.head)
        self.assert_active(self.head, True)

    def test_team_lead_cannot_deactivate_admin(self):
        self.toggle(self.lead, self.admin)
        self.assert_active(self.admin, True)

    def test_team_lead_cannot_deactivate_another_team_lead(self):
        other = make_user('h_lead2', Profile.ROLE_TEAM_LEAD, 'Ngozi')
        self.toggle(self.lead, other)
        self.assert_active(other, True)

    def test_department_head_cannot_deactivate_admin(self):
        self.toggle(self.head, self.admin)
        self.assert_active(self.admin, True)

    def test_team_lead_can_deactivate_staff(self):
        self.toggle(self.lead, self.staff)
        self.assert_active(self.staff, False)

    def test_admin_can_deactivate_department_head(self):
        self.toggle(self.admin, self.head)
        self.assert_active(self.head, False)

    def test_team_lead_cannot_reactivate_department_head(self):
        self.head.is_active = False
        self.head.save(update_fields=['is_active'])
        self.toggle(self.lead, self.head)
        self.assert_active(self.head, False)

    # --- editing -----------------------------------------------------------

    def test_team_lead_cannot_edit_admin(self):
        response = self.client_for(self.lead).post(
            reverse('staff-edit', args=[self.admin.pk]),
            {'full_name': 'Bola Test', 'email': self.admin.email,
             'role': Profile.ROLE_STAFF},
        )
        self.assertRedirects(response, reverse('portal-staff'))
        self.admin.profile.refresh_from_db()
        self.assertEqual(self.admin.profile.role, Profile.ROLE_ADMIN)

    def test_team_lead_cannot_promote_themselves(self):
        self.client_for(self.lead).post(
            reverse('staff-edit', args=[self.lead.pk]),
            {'full_name': 'Ada Test', 'email': self.lead.email,
             'role': Profile.ROLE_ADMIN},
        )
        self.lead.profile.refresh_from_db()
        self.assertEqual(self.lead.profile.role, Profile.ROLE_TEAM_LEAD)

    def test_team_lead_cannot_promote_staff_beyond_their_own_rank(self):
        self.client_for(self.lead).post(
            reverse('staff-edit', args=[self.staff.pk]),
            {'full_name': 'Tunde Test', 'email': self.staff.email,
             'role': Profile.ROLE_DEPT_HEAD},
        )
        self.staff.profile.refresh_from_db()
        self.assertEqual(self.staff.profile.role, Profile.ROLE_STAFF)

    # --- creation ----------------------------------------------------------

    def test_team_lead_cannot_create_a_department_head(self):
        self.client_for(self.lead).post(
            reverse('staff-create'),
            {'full_name': 'New Head', 'email': 'h_newhead@dash-mfb.com',
             'role': Profile.ROLE_DEPT_HEAD},
        )
        self.assertFalse(User.objects.filter(email='h_newhead@dash-mfb.com').exists())

    def test_admin_can_create_another_admin(self):
        self.client_for(self.admin).post(
            reverse('staff-create'),
            {'full_name': 'New Admin', 'email': 'h_newadmin@dash-mfb.com',
             'role': Profile.ROLE_ADMIN},
        )
        created = User.objects.get(email='h_newadmin@dash-mfb.com')
        self.assertEqual(created.profile.role, Profile.ROLE_ADMIN)

    # --- helpers -----------------------------------------------------------

    def test_rank_rules(self):
        self.assertTrue(can_manage(self.admin, self.admin))
        self.assertFalse(can_manage(self.head, self.head))
        self.assertFalse(can_manage(self.lead, self.head))
        self.assertTrue(can_manage(self.head, self.lead))

        lead_roles = [role for role, _ in assignable_roles(self.lead)]
        self.assertEqual(lead_roles, [Profile.ROLE_STAFF])
        head_roles = [role for role, _ in assignable_roles(self.head)]
        self.assertNotIn(Profile.ROLE_DEPT_HEAD, head_roles)
        self.assertNotIn(Profile.ROLE_ADMIN, head_roles)


class AuditorIsManagedByAdminOnlyTests(TestCase):
    """The Auditor sits outside the chain of command in both directions.

    A lead or head who could deactivate their auditor could switch off the
    oversight they are subject to, so only an Admin manages that account.
    """

    def setUp(self):
        self.admin = make_user('a_admin', Profile.ROLE_ADMIN, 'Bola')
        self.head = make_user('a_head', Profile.ROLE_DEPT_HEAD, 'Chika')
        self.lead = make_user('a_lead', Profile.ROLE_TEAM_LEAD, 'Ada')
        self.staff = make_user('a_staff', Profile.ROLE_STAFF, 'Tunde')
        self.auditor = make_user('a_auditor', Profile.ROLE_AUDITOR, 'Ife')

    def client_for(self, user):
        client = self.client_class()
        self.assertTrue(client.login(username=user.email, password=PW))
        return client

    def toggle(self, actor, target):
        return self.client_for(actor).post(reverse('staff-toggle', args=[target.pk]))

    def test_team_lead_cannot_deactivate_the_auditor(self):
        self.toggle(self.lead, self.auditor)
        self.auditor.refresh_from_db()
        self.assertTrue(self.auditor.is_active)

    def test_department_head_cannot_deactivate_the_auditor(self):
        self.toggle(self.head, self.auditor)
        self.auditor.refresh_from_db()
        self.assertTrue(self.auditor.is_active)

    def test_admin_can_deactivate_the_auditor(self):
        self.toggle(self.admin, self.auditor)
        self.auditor.refresh_from_db()
        self.assertFalse(self.auditor.is_active)

    def test_the_auditor_manages_nobody(self):
        for target in (self.staff, self.lead, self.head, self.admin):
            self.assertFalse(can_manage(self.auditor, target),
                             f'auditor should not manage {target}')

    def test_only_an_admin_can_appoint_an_auditor(self):
        for actor in (self.lead, self.head):
            roles = [role for role, _ in assignable_roles(actor)]
            self.assertNotIn(Profile.ROLE_AUDITOR, roles)

        self.assertIn(Profile.ROLE_AUDITOR,
                      [role for role, _ in assignable_roles(self.admin)])

    def test_department_head_cannot_create_an_auditor_through_the_form(self):
        self.client_for(self.head).post(reverse('staff-create'), {
            'full_name': 'Sneaky Auditor', 'email': 'a_sneaky@dash-mfb.com',
            'role': Profile.ROLE_AUDITOR,
        })
        self.assertFalse(User.objects.filter(email='a_sneaky@dash-mfb.com').exists())

    def test_admin_can_create_an_auditor(self):
        self.client_for(self.admin).post(reverse('staff-create'), {
            'full_name': 'New Auditor', 'email': 'a_new@dash-mfb.com',
            'role': Profile.ROLE_AUDITOR,
        })
        created = User.objects.get(email='a_new@dash-mfb.com')
        self.assertEqual(created.profile.role, Profile.ROLE_AUDITOR)

    def test_an_auditor_cannot_promote_themselves(self):
        self.client_for(self.auditor).post(
            reverse('staff-edit', args=[self.auditor.pk]),
            {'full_name': 'Ife Test', 'email': self.auditor.email,
             'role': Profile.ROLE_ADMIN},
        )
        self.auditor.profile.refresh_from_db()
        self.assertEqual(self.auditor.profile.role, Profile.ROLE_AUDITOR)


class AuditorSitsInNoTeamTests(TestCase):
    """Team membership is who reviews your work. An Auditor has none.

    The team form already leaves auditors out of its member list, so an
    auditor who slipped into a team through the staff form would be dropped
    again the next time anybody edited that team - two screens disagreeing
    about the same row.
    """

    def setUp(self):
        self.admin = make_user('t_admin', Profile.ROLE_ADMIN, 'Bola')
        self.lead = make_user('t_lead', Profile.ROLE_TEAM_LEAD, 'Ada')
        self.staff = make_user('t_staff', Profile.ROLE_STAFF, 'Tunde')
        self.team = Team.objects.create(name='Team Under Audit', lead=self.lead)

    def client_for(self, user):
        client = self.client_class()
        self.assertTrue(client.login(username=user.email, password=PW))
        return client

    def test_an_auditor_cannot_be_created_into_a_team(self):
        self.client_for(self.admin).post(reverse('staff-create'), {
            'full_name': 'Ife Auditor', 'email': 't_ife@dash-mfb.com',
            'role': Profile.ROLE_AUDITOR, 'teams': [self.team.pk],
        })
        self.assertFalse(User.objects.filter(email='t_ife@dash-mfb.com').exists())

    def test_an_auditor_can_be_created_without_one(self):
        self.client_for(self.admin).post(reverse('staff-create'), {
            'full_name': 'Ife Auditor', 'email': 't_ife@dash-mfb.com',
            'role': Profile.ROLE_AUDITOR,
        })
        created = User.objects.get(email='t_ife@dash-mfb.com')
        self.assertEqual(created.profile.role, Profile.ROLE_AUDITOR)
        self.assertEqual(created.profile.teams.count(), 0)

    def test_turning_a_team_member_into_an_auditor_is_refused_until_they_leave(self):
        self.staff.profile.teams.add(self.team)

        response = self.client_for(self.admin).post(
            reverse('staff-edit', args=[self.staff.pk]),
            {'full_name': 'Tunde Test', 'email': self.staff.email,
             'role': Profile.ROLE_AUDITOR, 'teams': [self.team.pk]},
        )
        self.assertContains(response, 'sits in none')
        self.staff.profile.refresh_from_db()
        self.assertEqual(self.staff.profile.role, Profile.ROLE_STAFF)

        # Untick the team and the same change goes through.
        self.client_for(self.admin).post(
            reverse('staff-edit', args=[self.staff.pk]),
            {'full_name': 'Tunde Test', 'email': self.staff.email,
             'role': Profile.ROLE_AUDITOR},
        )
        self.staff.profile.refresh_from_db()
        self.assertEqual(self.staff.profile.role, Profile.ROLE_AUDITOR)
        self.assertEqual(self.staff.profile.teams.count(), 0)

    def test_the_team_form_will_not_take_an_auditor_as_a_member(self):
        from .forms import TeamForm
        auditor = make_user('t_auditor', Profile.ROLE_AUDITOR, 'Ife')

        form = TeamForm(
            {'name': self.team.name, 'lead': self.lead.pk, 'members': [auditor.pk]},
            instance=self.team,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('members', form.errors)

    def test_the_team_form_will_not_take_an_auditor_as_the_lead(self):
        from .forms import TeamForm
        auditor = make_user('t_auditor2', Profile.ROLE_AUDITOR, 'Ife')

        form = TeamForm({'name': 'New Team', 'lead': auditor.pk, 'members': []})
        self.assertFalse(form.is_valid())
        self.assertIn('lead', form.errors)
