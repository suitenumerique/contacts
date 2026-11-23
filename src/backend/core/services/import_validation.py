"""Validation of an uploaded import file."""

from django.core.exceptions import ValidationError

from core.services.vcf_service import contains_vcard


def validate_import_file(file_format, data):
    """Reject *data* when it cannot be a *file_format* import file.

    The checks use only the content. The client chooses the file name and
    the Content-Type of the upload. A valid export can have any name and
    any Content-Type.
    """
    if not data.strip():
        raise ValidationError("The file is empty.", code="empty_file")
    # The parsers read UTF-8 and Windows-1252 text. This text has no NUL byte.
    if b"\0" in data:
        raise ValidationError("The file is not a text file.", code="not_text")
    if file_format == "vcf" and not contains_vcard(data):
        raise ValidationError("The file contains no vCard.", code="no_vcard")
