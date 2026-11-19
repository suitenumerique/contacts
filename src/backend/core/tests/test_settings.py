"""
Unit tests for the settings validation
"""

import pytest

from core.entitlements.backends.deploycenter import DeployCenterEntitlementsBackend

from contacts.settings import Base

# The path of the real class, so that a move of the class fails these tests.
DEPLOYCENTER = (
    f"{DeployCenterEntitlementsBackend.__module__}."
    f"{DeployCenterEntitlementsBackend.__qualname__}"
)
LOCAL = "core.entitlements.backends.local.LocalEntitlementsBackend"


def test_invalid_settings_oidc_email_configuration():
    """
    The OIDC_FALLBACK_TO_EMAIL_FOR_IDENTIFICATION and OIDC_ALLOW_DUPLICATE_EMAILS settings
    should not be both set to True simultaneously.
    """

    class TestSettings(Base):
        """Fake test settings."""

        OIDC_FALLBACK_TO_EMAIL_FOR_IDENTIFICATION = True
        OIDC_ALLOW_DUPLICATE_EMAILS = True

    # The validation is performed during post_setup
    with pytest.raises(ValueError) as excinfo:
        TestSettings().post_setup()

    # Check the exception message
    assert str(excinfo.value) == (
        "Both OIDC_FALLBACK_TO_EMAIL_FOR_IDENTIFICATION and "
        "OIDC_ALLOW_DUPLICATE_EMAILS cannot be set to True simultaneously. "
    )


@pytest.mark.parametrize(
    "stored_claims, organization_claim, setting_in_error",
    [
        ([], "siret", "OIDC_STORE_CLAIMS"),
        (["email"], "siret", "OIDC_STORE_CLAIMS"),
        (["siret"], "", "OIDC_USERINFO_ORGANIZATION_CLAIM"),
        (["siret"], "idp_id", "OIDC_USERINFO_ORGANIZATION_CLAIM"),
    ],
)
def test_invalid_settings_entitlement_claims(
    stored_claims, organization_claim, setting_in_error
):
    """
    A claim that the entitlements backend sends must be a stored claim.
    The organization claim must be one of the claims that the backend sends.
    """

    class TestSettings(Base):
        """Fake test settings."""

        ENTITLEMENTS_BACKEND_PARAMETERS = {"oidc_claims": ["siret"]}
        OIDC_STORE_CLAIMS = stored_claims
        OIDC_USERINFO_ORGANIZATION_CLAIM = organization_claim

    with pytest.raises(ValueError, match=f"^{setting_in_error} must"):
        TestSettings().post_setup()


@pytest.mark.parametrize(
    "parameters", [{}, {"oidc_claims": []}, {"oidc_claims": ["email"]}]
)
def test_invalid_settings_deploycenter_without_siret(parameters):
    """The DeployCenter backend must send the siret claim."""

    class TestSettings(Base):
        """Fake test settings."""

        ENTITLEMENTS_BACKEND = DEPLOYCENTER
        ENTITLEMENTS_BACKEND_PARAMETERS = parameters
        OIDC_STORE_CLAIMS = ["email"]
        OIDC_USERINFO_ORGANIZATION_CLAIM = "email"

    with pytest.raises(ValueError, match="^ENTITLEMENTS_BACKEND_PARAMETERS must"):
        TestSettings().post_setup()


@pytest.mark.parametrize(
    "backend, parameters, stored_claims, organization_claim",
    [
        (DEPLOYCENTER, {"oidc_claims": ["siret"]}, ["siret", "email"], "siret"),
        # A backend that sends no claim puts no rule on the other settings.
        (LOCAL, {}, [], ""),
        (LOCAL, {"oidc_claims": None}, [], "siret"),
    ],
)
def test_valid_settings_entitlement_claims(
    backend, parameters, stored_claims, organization_claim
):
    """Entitlement settings that agree pass the validation."""

    class TestSettings(Base):
        """Fake test settings."""

        ENTITLEMENTS_BACKEND = backend
        ENTITLEMENTS_BACKEND_PARAMETERS = parameters
        OIDC_STORE_CLAIMS = stored_claims
        OIDC_USERINFO_ORGANIZATION_CLAIM = organization_claim

    TestSettings().post_setup()
