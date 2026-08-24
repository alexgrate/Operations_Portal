"""Tests for the users app.

Run with:  python manage.py test users
"""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Profile, assignable_roles, can_manage

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
