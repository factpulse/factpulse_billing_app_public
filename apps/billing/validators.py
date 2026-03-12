from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator

validate_image_extension = FileExtensionValidator(
    allowed_extensions=["jpg", "jpeg", "png", "webp"]
)


def validate_image_size(value):
    if value.size > 2 * 1024 * 1024:  # 2 MB
        raise ValidationError("Image file size must be under 2 MB.")


validate_pdf_extension = FileExtensionValidator(allowed_extensions=["pdf"])


def validate_pdf_size(value):
    if value.size > 20 * 1024 * 1024:  # 20 MB
        raise ValidationError("PDF file size must be under 20 MB.")
