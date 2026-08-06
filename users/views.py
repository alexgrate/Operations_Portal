from django.contrib import messages
from django.shortcuts import redirect, render

from .forms import SignUpForm


def register(request):
    if request.user.is_authenticated:
        return redirect('portal-dashboard')

    if request.method == 'POST':
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(
                request,
                f'Account created for {user.email}. Please log in to continue.',
            )
            return redirect('portal-login')
        messages.error(request, 'We could not create your account. Please check the fields below.')
    else:
        form = SignUpForm()

    return render(request, 'users/register.html', {'form': form})
