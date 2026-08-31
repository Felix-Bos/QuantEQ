from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from users.forms import LoginForm, ProfileForm, SignupForm


def login_view(request):
    if request.method == 'POST':
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            return redirect('/analysis/')
    else:
        form = LoginForm(request)
    return render(request, 'users/login.html', {'form': form})


def signup_view(request):
    if request.method == 'POST':
        form = SignupForm(data=request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('/analysis/')
    else:
        form = SignupForm()
    return render(request, 'users/signup.html', {'form': form})


def logout_view(request):
    logout(request)
    return redirect('/auth/login/')


@login_required
def profile_view(request):
    if request.method == 'POST':
        form = ProfileForm(data=request.POST, instance=request.user)
        if form.is_valid():
            password_changed = form.changes_password
            user = form.save()
            if password_changed:
                update_session_auth_hash(request, user)
            messages.success(request, 'Your profile has been updated.')
            return redirect('profile')
    else:
        form = ProfileForm(instance=request.user)

    return render(
        request,
        'users/profile.html',
        {'form': form, 'active_module': 'profile'},
    )
