"""Staff onboarding: an Admin adds someone, they set their own password."""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth import views as auth_views
from django.core.mail import EmailMultiAlternatives
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.views.decorators.http import require_POST

from portal.pagination import paginate
from portal.views import management_required

from .forms import StaffForm, TeamForm
from .models import Team, can_manage
from .tokens import invite_token_generator


def send_invite(request, user):
    """Email a link that lets this person set their own password."""
    context = {
        'user': user,
        'uid': urlsafe_base64_encode(force_bytes(user.pk)),
        'token': invite_token_generator.make_token(user),
        'domain': request.get_host(),
        'protocol': 'https' if request.is_secure() else 'http',
        'expiry_days': getattr(settings, 'INVITE_LINK_TIMEOUT', 604800) // 86400,
    }
    message = EmailMultiAlternatives(
        subject=render_to_string('users/invite_subject.txt', context).strip(),
        body=render_to_string('users/invite_email.txt', context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )
    message.attach_alternative(render_to_string('users/invite_email.html', context), 'text/html')
    message.send(fail_silently=False)

    from django.utils import timezone
    profile = user.profile
    profile.invite_sent_at = timezone.now()
    profile.save(update_fields=['invite_sent_at'])


@login_required
@management_required
def staff_create(request):
    if request.method == 'POST':
        form = StaffForm(request.POST, actor=request.user)
        if form.is_valid():
            user = form.save()
            try:
                send_invite(request, user)
                messages.success(
                    request,
                    f'{user.get_full_name()} added. Invite sent to {user.email}.',
                )
            except Exception:
                # The account exists either way. invite_sent_at stays empty, so
                # the staff list shows "Not invited" with a Send invite button.
                messages.warning(
                    request,
                    f'{user.get_full_name()} was added, but the invite could not be '
                    f'emailed. Use Send invite on the staff list to try again.',
                )
            return redirect('portal-staff')
        messages.error(request, 'Could not add them - check the highlighted fields.')
    else:
        form = StaffForm(actor=request.user)

    return render(request, 'portal/staff_form.html', {
        'form': form, 'active_tab': 'staff', 'heading': 'Add staff',
    })


@login_required
@management_required
def staff_edit(request, pk):
    person = get_object_or_404(User, pk=pk)

    if not can_manage(request.user, person):
        messages.error(request, 'You can only manage people ranked below your own role.')
        return redirect('portal-staff')

    if request.method == 'POST':
        form = StaffForm(request.POST, instance=person, actor=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Saved.')
            return redirect('portal-staff')
        messages.error(request, 'Could not save - check the highlighted fields.')
    else:
        form = StaffForm(instance=person, actor=request.user)

    return render(request, 'portal/staff_form.html', {
        'form': form, 'person': person, 'active_tab': 'staff',
        'heading': f'Edit {person.get_full_name() or person.username}',
    })


@login_required
@management_required
@require_POST
def staff_resend_invite(request, pk):
    person = get_object_or_404(User, pk=pk)

    if not can_manage(request.user, person):
        messages.error(request, 'You can only manage people ranked below your own role.')
        return redirect('portal-staff')

    if person.has_usable_password():
        messages.info(request, f'{person.get_full_name()} has already set a password.')
        return redirect('portal-staff')

    try:
        first_time = person.profile.invite_sent_at is None

        send_invite(request, person)

        messages.success(
                request,
                f'Invite {"sent" if first_time else "re-sent"} to {person.email}.',
            )
    except Exception:
        messages.error(request, 'The invite email could not be sent. Check the mail settings.')
    return redirect('portal-staff')


@login_required
@management_required
@require_POST
def staff_deactivate(request, pk):
    person = get_object_or_404(User, pk=pk)

    if person == request.user:
        messages.error(request, 'You cannot deactivate your own account.')
    elif not can_manage(request.user, person):
        messages.error(request, 'You can only manage people ranked below your own role.')
    elif person.teams_led.exists():
        messages.error(request, 'They still lead a team. Reassign it first.')
    else:
        person.is_active = not person.is_active
        person.save(update_fields=['is_active'])
        messages.success(
            request,
            f'{person.get_full_name()} {"reactivated" if person.is_active else "deactivated"}.',
        )
    return redirect('portal-staff')


class InviteAcceptView(auth_views.PasswordResetConfirmView):
    """Where an invite link lands: set a password for the first time."""
    template_name = 'users/invite_confirm.html'
    token_generator = invite_token_generator
    success_url = reverse_lazy('portal-login')
    post_reset_login = False


@login_required
@management_required
def team_list(request):
    """Every team, who leads it, and what it is carrying."""
    # Retired teams stay listed so they can be brought back.
    teams = (
        Team.objects.select_related('lead')
        .prefetch_related('members__user', 'tasks')
        .order_by('-is_active', 'name')
    )

    rows = []
    for team in teams:
        open_tasks = [t for t in team.tasks.all() if t.is_open]
        rows.append({
            'team': team,
            'members': [p.user for p in team.members.all()],
            'open': len(open_tasks),
            'overdue': sum(1 for t in open_tasks if t.urgency == 'overdue'),
        })

    page = paginate(request, rows)
    return render(request, 'portal/teams.html',
                  {'rows': page['items'], 'active_tab': 'teams', **page})


@login_required
@management_required
def team_create(request):
    if request.method == 'POST':
        form = TeamForm(request.POST)
        if form.is_valid():
            team = form.save()
            messages.success(request, f'{team.name} created.')
            return redirect('portal-teams')
        messages.error(request, 'Could not save - check the highlighted fields.')
    else:
        form = TeamForm()

    return render(request, 'portal/team_form.html', {
        'form': form,
        'heading': 'New team',
        'active_tab': 'teams',
    })


@login_required
@management_required
def team_edit(request, pk):
    team = get_object_or_404(Team, pk=pk)

    if request.method == 'POST':
        form = TeamForm(request.POST, instance=team)
        if form.is_valid():
            form.save()
            messages.success(request, 'Team updated.')
            return redirect('portal-teams')
        messages.error(request, 'Could not save - check the highlighted fields.')
    else:
        form = TeamForm(instance=team)

    return render(request, 'portal/team_form.html', {
        'form': form,
        'team': team,
        'heading': team.name,
        'active_tab': 'teams',
    })


@login_required
@management_required
@require_POST
def team_toggle(request, pk):
    """Retire a team, or bring one back.

    Never deleted. Tasks point at their team with PROTECT precisely so the
    record of who reviewed what cannot be erased by tidying up.
    """
    team = get_object_or_404(Team, pk=pk)

    if team.is_active:
        open_count = sum(1 for t in team.tasks.all() if t.is_open)
        if open_count:
            messages.error(
                request,
                f'{team.name} still has {open_count} open task(s). '
                'Finish or reassign them before retiring it.',
            )
            return redirect('portal-teams')

    team.is_active = not team.is_active
    team.save(update_fields=['is_active'])

    messages.success(
        request,
        f'{team.name} {"brought back" if team.is_active else "retired"}.',
    )
    return redirect('portal-teams')
