from django.urls import path

from .views import support_ticket_create, support_ticket_detail, support_ticket_list


urlpatterns = [
    path("", support_ticket_list, name="support-ticket-list"),
    path("new/", support_ticket_create, name="support-ticket-create"),
    path("<int:ticket_id>/", support_ticket_detail, name="support-ticket-detail"),
]
