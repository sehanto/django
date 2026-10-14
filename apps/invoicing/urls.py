from django.urls import path

from . import views

app_name = "invoicing"

urlpatterns = [
    path("payments/", views.PaymentListView.as_view(), name="payments"),
    path("payments/<int:pk>/void/", views.PaymentVoidView.as_view(), name="payment_void"),
    path("payments/<int:pk>/bukti-potong/", views.WithholdingSlipView.as_view(), name="payment_slip"),
    path("returns/<int:pk>/", views.ReturnDetailView.as_view(), name="return_detail"),
    path("returns/<int:pk>/void/", views.ReturnVoidView.as_view(), name="return_void"),
    path("write-offs/<int:pk>/void/", views.WriteOffVoidView.as_view(), name="write_off_void"),
    path("returns/<int:pk>/coretax.xml", views.ReturnCoretaxView.as_view(), name="return_coretax"),
    path("returns/coretax/<int:year>/<int:month>.xml", views.MonthCoretaxView.as_view(), name="month_coretax"),
    path("faktur-pajak-keluaran/<int:year>/<int:month>.xml", views.MonthInvoicesCoretaxView.as_view(), name="month_invoices_coretax"),
    path("ppn-digunggung/<int:year>/<int:month>.xml", views.MonthRetailCoretaxView.as_view(), name="month_retail_coretax"),
    path("dokumen-lain/<int:year>/<int:month>/<str:kind>.xml", views.MonthSpecialDocumentsView.as_view(), name="month_special_documents"),
    path("coretax/", views.CoretaxConnectionView.as_view(), name="coretax_connection"),
    path("coretax/send/<int:year>/<int:month>/", views.CoretaxSendView.as_view(), name="coretax_send_month"),
    path("coretax/send/<int:year>/<int:month>/returns/", views.CoretaxSendView.as_view(), {"returns": True}, name="coretax_send_returns"),
    path("coretax/send/invoice/<int:pk>/", views.CoretaxSendView.as_view(), name="coretax_send_invoice"),
    path("coretax/submissions/<int:pk>/refresh/", views.CoretaxRefreshView.as_view(), name="coretax_refresh"),
    path("items/", views.ItemListView.as_view(), name="items"),
    path("items/new/", views.ItemCreateView.as_view(), name="item_create"),
    path("items/<int:pk>/edit/", views.ItemUpdateView.as_view(), name="item_update"),
    path("<invoice_kind:kind>/", views.InvoiceListView.as_view(), name="list"),
    path("<invoice_kind:kind>/new/", views.InvoiceCreateView.as_view(), name="create"),
    path("<invoice_kind:kind>/<int:pk>/", views.InvoiceDetailView.as_view(), name="detail"),
    path("<invoice_kind:kind>/<int:pk>/coretax.xml", views.InvoiceCoretaxView.as_view(), name="coretax"),
    path("<invoice_kind:kind>/<int:pk>/faktur-number/", views.TaxInvoiceNumberView.as_view(), name="tax_invoice_number"),
    path("<invoice_kind:kind>/<int:pk>/document/", views.SpecialDocumentNumberView.as_view(), name="special_document"),
    path("<invoice_kind:kind>/<int:pk>/print/", views.InvoicePrintView.as_view(), name="print"),
    path("<invoice_kind:kind>/<int:pk>/edit/", views.InvoiceUpdateView.as_view(), name="update"),
    path("<invoice_kind:kind>/<int:pk>/issue/", views.InvoiceIssueView.as_view(), name="issue"),
    path("<invoice_kind:kind>/<int:pk>/void/", views.InvoiceVoidView.as_view(), name="void"),
    path("<invoice_kind:kind>/<int:pk>/write-off/", views.InvoiceWriteOffView.as_view(), name="write_off"),
    path("<invoice_kind:kind>/<int:pk>/delete/", views.InvoiceDeleteView.as_view(), name="delete"),
    path("<invoice_kind:kind>/<int:pk>/pay/", views.PaymentCreateView.as_view(), name="pay"),
    path("<invoice_kind:kind>/<int:pk>/return/", views.ReturnCreateView.as_view(), name="return_create"),
]
