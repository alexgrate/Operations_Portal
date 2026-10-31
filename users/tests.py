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


# --- Single sign-on through OCI IAM -----------------------------------------

from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.test import override_settings

from . import sso

OCI = dict(
    OCI_SSO_ISSUER_URL='https://idcs-test.identity.oraclecloud.com',
    OCI_SSO_CLIENT_ID='portal-client',
    OCI_SSO_CLIENT_SECRET='portal-secret',
    OCI_SSO_SCOPES=['openid', 'email', 'profile'],
    OCI_SSO_REDIRECT_URI='http://testserver/sso/callback/',
)

ENDPOINTS = {
    'authorize': 'https://idcs-test.identity.oraclecloud.com/oauth2/v1/authorize',
    'token': 'https://idcs-test.identity.oraclecloud.com/oauth2/v1/token',
    'userinfo': 'https://idcs-test.identity.oraclecloud.com/oauth2/v1/userinfo',
}


class LoginPageTests(TestCase):
    """The password login is never removed, only folded away."""

    @override_settings(OCI_SSO_ISSUER_URL='', OCI_SSO_CLIENT_ID='', OCI_SSO_CLIENT_SECRET='')
    def test_without_sso_the_page_is_the_password_form_alone(self):
        html = self.client.get(reverse('portal-login')).content.decode()
        self.assertNotIn('Sign in with OCI', html)
        self.assertNotIn('Administrator sign-in', html)
        self.assertIn('name="password"', html)

    @override_settings(**OCI)
    def test_with_sso_the_password_form_is_folded_under_administrator_sign_in(self):
        html = self.client.get(reverse('portal-login')).content.decode()
        self.assertIn('Sign in with OCI', html)
        self.assertIn('Administrator sign-in', html)
        self.assertIn('name="password"', html)
        self.assertNotIn('<details class="admin-signin" open>', html)

    @override_settings(**OCI)
    def test_a_failed_password_attempt_reopens_the_form(self):
        response = self.client.post(reverse('portal-login'),
                                    {'email': 'x@dash-mfb.com', 'password': 'wrong'})
        self.assertIn('<details class="admin-signin" open>', response.content.decode())

    @override_settings(**OCI)
    def test_the_password_login_still_works(self):
        make_user('pw_staff', Profile.ROLE_STAFF, 'Tunde')
        self.client.post(reverse('portal-login'),
                         {'email': 'pw_staff@dash-mfb.com', 'password': PW})
        self.assertIn('_auth_user_id', self.client.session)


@override_settings(**OCI)
@mock.patch.object(sso, 'discover', return_value=ENDPOINTS)
class OciSignInTests(TestCase):

    def setUp(self):
        self.user = make_user('sso_staff', Profile.ROLE_STAFF, 'Tunde')

    def start(self, next_url=None):
        url = reverse('sso-login') + (f'?next={next_url}' if next_url else '')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        return parse_qs(urlparse(response['Location']).query)

    def callback(self, state, email='sso_staff@dash-mfb.com', code='the-code'):
        def fake(request):
            if request.full_url == ENDPOINTS['token']:
                return {'access_token': 'oci-access-token'}
            return {'sub': 'ocid1.user.abc', 'email': email}

        with mock.patch.object(sso, '_request_json', side_effect=fake) as calls:
            response = self.client.get(reverse('sso-callback'),
                                       {'code': code, 'state': state})
        return response, calls

    def signed_in(self):
        return self.client.session.get('_auth_user_id')

    def test_start_sends_the_browser_to_oci_with_state_and_pkce(self, _):
        params = self.start()
        self.assertEqual(params['client_id'], ['portal-client'])
        self.assertEqual(params['redirect_uri'], ['http://testserver/sso/callback/'])
        self.assertEqual(params['response_type'], ['code'])
        self.assertEqual(params['code_challenge_method'], ['S256'])
        self.assertTrue(params['state'][0])

    def test_a_known_corporate_account_is_signed_in(self, _):
        state = self.start(next_url='/app/catalog/')['state'][0]
        response, calls = self.callback(state)

        self.assertEqual(self.signed_in(), str(self.user.pk))
        self.assertEqual(response['Location'], '/app/catalog/')

        token_request = calls.call_args_list[0].args[0]
        body = parse_qs(token_request.data.decode())
        self.assertEqual(body['grant_type'], ['authorization_code'])
        self.assertIn('code_verifier', body)
        self.assertTrue(token_request.get_header('Authorization').startswith('Basic '))

    def test_a_state_from_another_browser_is_refused(self, _):
        self.start()
        self.callback('somebody-elses-state')
        self.assertIsNone(self.signed_in())

    def test_a_callback_with_no_attempt_started_is_refused(self, _):
        self.callback('anything')
        self.assertIsNone(self.signed_in())

    def test_an_attempt_completes_once_only(self, _):
        state = self.start()['state'][0]
        self.callback(state)
        self.client.logout()
        self.callback(state)
        self.assertIsNone(self.signed_in())

    def test_a_stale_attempt_is_refused(self, _):
        state = self.start()['state'][0]
        session = self.client.session
        session['oci_sso_attempt']['started'] -= 11 * 60
        session.save()
        self.callback(state)
        self.assertIsNone(self.signed_in())

    def test_nobody_is_created_for_an_unknown_address(self, _):
        state = self.start()['state'][0]
        self.callback(state, email='stranger@dash-mfb.com')
        self.assertIsNone(self.signed_in())
        self.assertFalse(User.objects.filter(email='stranger@dash-mfb.com').exists())

    def test_a_deactivated_account_is_refused(self, _):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        state = self.start()['state'][0]
        self.callback(state)
        self.assertIsNone(self.signed_in())

    def test_a_non_corporate_address_is_refused(self, _):
        state = self.start()['state'][0]
        self.callback(state, email='sso_staff@gmail.com')
        self.assertIsNone(self.signed_in())

    def test_oci_reporting_an_error_is_refused(self, _):
        self.start()
        self.client.get(reverse('sso-callback'), {'error': 'access_denied'})
        self.assertIsNone(self.signed_in())


# --- Reset and invite links -------------------------------------------------

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator

from .tokens import invite_token_generator


class ResetLinkTests(TestCase):

    def setUp(self):
        self.user = make_user('link_staff', Profile.ROLE_STAFF, 'Tunde')

    def test_a_reset_link_lasts_one_hour(self):
        self.assertEqual(settings.PASSWORD_RESET_TIMEOUT, 60 * 60)

    def test_a_reset_token_is_not_an_invite_token(self):
        """Otherwise opening a reset link at /invite/ stretches it to 7 days."""
        token = default_token_generator.make_token(self.user)
        self.assertFalse(invite_token_generator.check_token(self.user, token))

    def test_an_invite_token_is_not_a_reset_token(self):
        token = invite_token_generator.make_token(self.user)
        self.assertFalse(default_token_generator.check_token(self.user, token))
        self.assertTrue(invite_token_generator.check_token(self.user, token))
