"""Domain errors for the invoicing package."""


class InvoiceError(Exception):
    """Base class for every invoicing domain error."""


class ValidationError(InvoiceError):
    """Raised when invoice input data is invalid."""


class PaymentError(InvoiceError):
    """Raised when a payment cannot be applied."""
