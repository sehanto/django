from django.urls import path

from . import views

app_name = "payroll"

urlpatterns = [
    path("", views.PayrollView.as_view(), name="index"),
    path("settings/", views.PayrollSettingsView.as_view(), name="settings"),
    path("employees/", views.EmployeeListView.as_view(), name="employees"),
    path("employees/new/", views.EmployeeCreateView.as_view(), name="employee_create"),
    path("employees/<int:pk>/edit/", views.EmployeeUpdateView.as_view(), name="employee_update"),
    path("employees/<int:pk>/prior-income/", views.PriorIncomeCreateView.as_view(), name="prior_income_create"),
    path("prior-income/<int:pk>/delete/", views.PriorIncomeDeleteView.as_view(), name="prior_income_delete"),
    path("<year:year>/<month:month>/new/", views.PayrollRunCreateView.as_view(), name="run_create"),
    path("runs/<int:pk>/", views.PayrollRunView.as_view(), name="run"),
    path("runs/<int:pk>/pph21.<export_format:export_format>", views.PayrollRunView.as_view(), name="run_download"),
    path("runs/<int:pk>/<str:action>/", views.PayrollActionView.as_view(), name="run_action"),
    path("<year:year>/annual/", views.AnnualView.as_view(), name="annual"),
    path("<year:year>/annual/1721-a1.<export_format:export_format>", views.AnnualView.as_view(), name="annual_download"),
    path("<year:year>/annual/<int:pk>/", views.AnnualStatementView.as_view(), name="a1"),
    path("payslips/<int:pk>/", views.PayslipView.as_view(), name="payslip"),
    path("payslips/<int:pk>/bukti-potong/", views.PayslipSlipNumberView.as_view(), name="payslip_slip"),
]
