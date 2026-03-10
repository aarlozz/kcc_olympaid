from django.urls import path
from . import views

urlpatterns = [
    # Auth
    path('', views.team_login, name='login'),
    path('logout/', views.team_logout, name='logout'),

    # Team pages
    path('dashboard/', views.team_dashboard, name='team_dashboard'),
    path('request-question/', views.request_question, name='request_question'),
    path('answer/<int:request_id>/', views.answer_question, name='answer_question'),
    path('check-approval/', views.check_approval, name='check_approval'),

    # Public pages
    path('leaderboard/', views.public_leaderboard, name='leaderboard'),
    path('results/', views.final_results, name='final_results'),

    # Admin auth
    path('admin-panel/login/', views.admin_login, name='admin_login'),
    path('admin-panel/logout/', views.admin_logout, name='admin_logout'),

    # Admin panel
    path('admin-panel/', views.admin_dashboard, name='admin_dashboard'),
    path('admin-panel/teams/', views.admin_teams, name='admin_teams'),
    path('admin-panel/questions/', views.admin_questions, name='admin_questions'),
    path('admin-panel/requests/', views.admin_requests, name='admin_requests'),
    path('admin-panel/scoring/', views.admin_scoring, name='admin_scoring'),
    path('admin-panel/settings/', views.admin_settings, name='admin_settings'),
    path('admin-panel/leaderboard/', views.admin_leaderboard, name='admin_leaderboard'),

    # API
    path('api/leaderboard/', views.api_leaderboard, name='api_leaderboard'),
    path('api/pending-requests/', views.api_pending_requests, name='api_pending_requests'),
]