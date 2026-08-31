from django.test import TestCase
from django.urls import reverse

from users.models import User


class ProfileViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='old-name',
            email='old@example.com',
            password='Old-password-123',
        )

    def test_profile_requires_authentication(self):
        response = self.client.get(reverse('profile'))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('profile')}",
        )

    def test_profile_is_linked_from_the_user_name(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('profile'))

        self.assertContains(response, 'QuantEQ')
        self.assertContains(response, f'href="{reverse("profile")}"')

    def test_user_can_update_profile_information(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('profile'),
            {
                'username': 'new-name',
                'first_name': 'Ada',
                'last_name': 'Lovelace',
                'email': 'ada@example.com',
            },
        )

        self.assertRedirects(response, reverse('profile'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, 'new-name')
        self.assertEqual(self.user.first_name, 'Ada')
        self.assertEqual(self.user.last_name, 'Lovelace')
        self.assertEqual(self.user.email, 'ada@example.com')

    def test_user_can_change_password_and_remain_logged_in(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('profile'),
            {
                'username': self.user.username,
                'email': self.user.email,
                'current_password': 'Old-password-123',
                'new_password': 'New-password-456',
                'new_password_confirmation': 'New-password-456',
            },
        )

        self.assertRedirects(response, reverse('profile'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('New-password-456'))
        self.assertEqual(
            self.client.get(reverse('profile')).status_code,
            200,
        )

    def test_wrong_current_password_is_rejected(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('profile'),
            {
                'username': self.user.username,
                'email': self.user.email,
                'current_password': 'wrong-password',
                'new_password': 'New-password-456',
                'new_password_confirmation': 'New-password-456',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Current password is incorrect.')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Old-password-123'))
