from ninja.security import HttpBearer

from apps.core.models import PersonalAccessToken


class BearerAuth(HttpBearer):
    def authenticate(self, request, token):
        return PersonalAccessToken.authenticate(token)
