from django.urls import path
from users.views import login_view, logout_view, profile_view, signup_view

urlpatterns = [
    path('login/', login_view, name='login'),
    path('signup/', signup_view, name='signup'),
    path('profile/', profile_view, name='profile'),
    path('logout/', logout_view, name='logout'),
]
