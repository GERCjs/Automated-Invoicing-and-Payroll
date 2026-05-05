from django.urls import path

from .views import UserLoginView, UserLogoutView, create_admin_account, register

urlpatterns = [
    path("login/", UserLoginView.as_view(), name="login"),
    path("logout/", UserLogoutView.as_view(), name="logout"),
    path("register/", register, name="register"),
    path("admin-create/", create_admin_account, name="create-admin-account"),
]
