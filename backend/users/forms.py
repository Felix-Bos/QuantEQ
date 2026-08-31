from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from users.models import User


class LoginForm(AuthenticationForm):
    pass


class SignupForm(forms.Form):
    username = forms.CharField(max_length=150)
    email = forms.EmailField(required=True)
    password = forms.CharField(widget=forms.PasswordInput, strip=False)
    password2 = forms.CharField(widget=forms.PasswordInput, strip=False)

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError('Username already taken.')
        return username

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        password2 = cleaned_data.get('password2')
        if password and password2 and password != password2:
            self.add_error('password2', "Passwords do not match.")
        if password:
            try:
                validate_password(password)
            except forms.ValidationError as e:
                self.add_error('password', e)
        return cleaned_data

    def save(self):
        return User.objects.create_user(
            username=self.cleaned_data['username'],
            email=self.cleaned_data['email'],
            password=self.cleaned_data['password'],
        )


class ProfileForm(forms.ModelForm):
    current_password = forms.CharField(
        widget=forms.PasswordInput,
        required=False,
        strip=False,
    )
    new_password = forms.CharField(
        widget=forms.PasswordInput,
        required=False,
        strip=False,
    )
    new_password_confirmation = forms.CharField(
        widget=forms.PasswordInput,
        required=False,
        strip=False,
    )

    class Meta:
        model = User
        fields = ('username', 'first_name', 'last_name', 'email')

    def clean(self):
        cleaned_data = super().clean()
        current_password = cleaned_data.get('current_password')
        new_password = cleaned_data.get('new_password')
        confirmation = cleaned_data.get('new_password_confirmation')

        if not any((current_password, new_password, confirmation)):
            return cleaned_data

        if not current_password:
            self.add_error('current_password', 'Enter your current password.')
        elif not self.instance.check_password(current_password):
            self.add_error('current_password', 'Current password is incorrect.')

        if not new_password:
            self.add_error('new_password', 'Enter a new password.')
        else:
            try:
                validate_password(new_password, self.instance)
            except forms.ValidationError as error:
                self.add_error('new_password', error)

        if not confirmation:
            self.add_error(
                'new_password_confirmation',
                'Confirm your new password.',
            )
        elif new_password and new_password != confirmation:
            self.add_error(
                'new_password_confirmation',
                'Passwords do not match.',
            )

        return cleaned_data

    @property
    def changes_password(self):
        return bool(self.cleaned_data.get('new_password'))

    def save(self, commit=True):
        user = super().save(commit=False)
        new_password = self.cleaned_data.get('new_password')
        if new_password:
            user.set_password(new_password)
        if commit:
            user.save()
        return user
