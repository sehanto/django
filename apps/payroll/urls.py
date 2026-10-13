from django.urls import path

from . import views

app_name = "payroll"

urlpatterns = [
    path("", views.PayrollView.as_view(), name="index"),
    path("settings/", views.PayrollSettingsView.as_view(), name="settings"),
    path("employees/", views.EmployeeListView.as_view(), name="employees"),
    path("employees/new/", views.EmployeeCreateView.as_view(), name="employee_create"),
    path("employees/<int:pk>/edit/", views.EmployeeUpdateView.as_view(), name="employee_update"),
    path("<int:year>/<int:month>/new/", views.PayrollRunCreateView.as_view(), name="run_create"),
    path("runs/<int:pk>/", views.PayrollRunView.as_view(), name="run"),
    path("runs/<int:pk>/pph21.csv", views.PayrollRunView.as_view(), {"csv": True}, name="run_csv"),
    path("runs/<int:pk>/<str:action>/", views.PayrollActionView.as_view(), name="run_action"),
    path("payslips/<int:pk>/", views.PayslipView.as_view(), name="payslip"),
    path("payslips/<int:pk>/bukti-potong/", views.PayslipSlipNumberView.as_view(), name="payslip_slip"),
]
