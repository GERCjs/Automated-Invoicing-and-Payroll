from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


# This login backend lets a user log in with either username or email.
class EmailOrUsernameBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        user_model = get_user_model()
        # "identifier" can be a username or an email address.
        identifier = (username or kwargs.get(user_model.USERNAME_FIELD) or "").strip()
        if not identifier or password is None:
            return None

        # Look for a matching username OR matching email, ignoring uppercase/lowercase.
        user = (
            user_model._default_manager.filter(
                Q(username__iexact=identifier) | Q(email__iexact=identifier)
            )
            .order_by("id")
            .first()
        )
        if user is None:
            return None
        # Check the password and make sure the account is allowed to log in.
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
