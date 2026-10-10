"""Invoice lifecycle signals, for optional modules to react to (no receivers here).

invoice_validated — sent after the Factur-X is generated (status validated).
    kwargs: invoice
invoice_failed — sent when generation or transmission fails.
    kwargs: invoice, error_type, message
"""

from django.dispatch import Signal

invoice_validated = Signal()
invoice_failed = Signal()
