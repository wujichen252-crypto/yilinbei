from django.contrib.auth.hashers import check_password
from django.test import TestCase

from apps.core.models import PersonalAccessToken, User


class ModelServiceTests(TestCase):
    def test_laravel_bcrypt_and_token_expiration(self):
        user = User.objects.create_user("u", "pw", nickname="用户", type=0)
        self.assertTrue(check_password("pw", user.password))
        token, plain = PersonalAccessToken.issue(user)
        self.assertNotEqual(token.token, plain)
        self.assertEqual(PersonalAccessToken.authenticate(plain).id, user.id)

