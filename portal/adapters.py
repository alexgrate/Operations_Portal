from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.core.exceptions import PermissionDenied


class MySocialAccountAdapter(DefaultSocialAccountAdapter):

    def pre_social_login(self, request, sociallogin):
        email = sociallogin.user.email

        if not email:
            raise PermissionDenied(
                "Your Google account does not have an email address."
            )

        if not email.lower().endswith("@dash-mfb.com"):
            raise PermissionDenied(
                "Only Dash MFB corporate email accounts are allowed."
            )