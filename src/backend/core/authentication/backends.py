"""Authentication Backends for the Contacts core app."""

import logging

from django.conf import settings
from django.core.exceptions import SuspiciousOperation, ValidationError
from django.db import transaction

from lasuite.oidc_login.backends import (
    OIDCAuthenticationBackend as LaSuiteOIDCAuthenticationBackend,
)

from core.entitlements import EntitlementsUnavailableError, get_user_entitlements
from core.enums import ChannelScopeLevel
from core.models import (
    Channel,
    Contact,
    ContactGroup,
    ContactGroupMembership,
    ContactLabel,
    DuplicateEmailError,
    Organization,
)

logger = logging.getLogger(__name__)


def _resolve_org_external_id(claims, email=None):
    """Extract the organization external_id from OIDC claims or email domain.

    When ``OIDC_USERINFO_ORGANIZATION_CLAIM`` is configured, the claim
    MUST be present in the OIDC userinfo, with no fallback: silently
    falling back to the email domain would attach users to the wrong
    org. Returns ``None`` if the claim is missing so the caller can
    fail closed.

    When the setting is empty, fall back to the (lowercased) email domain
    so deployments without an org claim still work.
    """
    claim_key = settings.OIDC_USERINFO_ORGANIZATION_CLAIM
    if claim_key:
        return claims.get(claim_key) or None
    email = email or claims.get("email")
    return email.split("@")[-1].lower() if email and "@" in email else None


def get_or_create_organization(external_id, name=""):
    """Return the organization of *external_id* and if it is new.

    Two first logins of the same organization can run at the same time. The
    model validates the unique external_id before the insert, so the second
    creation raises ``ValidationError``, not the ``IntegrityError`` that
    ``get_or_create`` handles. Then the organization of the other login is used.
    """
    try:
        return Organization.objects.get_or_create(
            external_id=external_id, defaults={"name": name}
        )
    except ValidationError:
        org = Organization.objects.filter(external_id=external_id).first()
        if org is None:
            raise
        return org, False


def _move_user_data(user, old_org_id, new_org):
    """Carry *user*'s own address book over to *new_org*.

    Personal contacts, groups and user channels follow the user. Overlays
    of rows from the old organization, and links to its rows (group
    memberships, favorites), are dropped: those rows are no longer visible.
    """
    Contact.objects.filter(owner=user, overlay_of__organization_id=old_org_id).delete()
    Contact.objects.filter(owner=user).update(organization=new_org)
    ContactGroup.objects.filter(owner=user).update(organization=new_org)
    ContactGroupMembership.objects.filter(group__owner=user).exclude(
        contact__organization=new_org
    ).delete()
    ContactGroupMembership.objects.filter(
        contact__owner=user, group__organization_id=old_org_id
    ).delete()
    Contact.favorited_by.through.objects.filter(
        user=user, contact__organization_id=old_org_id
    ).delete()
    ContactGroup.favorited_by.through.objects.filter(
        user=user, contactgroup__organization_id=old_org_id
    ).delete()
    Channel.objects.filter(user=user, scope_level=ChannelScopeLevel.USER).update(
        organization=new_org
    )


def resolve_organization(user, claims, entitlements=None):
    """Resolve and assign the user's organization.

    The org identifier (external_id) comes from the OIDC claim configured via
    OIDC_USERINFO_ORGANIZATION_CLAIM, or falls back to the email domain.
    The org name is only ever set from a non-empty ``organization_name`` in
    the entitlements response.
    """
    entitlements = entitlements or {}
    external_id = _resolve_org_external_id(claims, email=user.email)
    if not external_id:
        logger.error(
            "Cannot resolve organization for user %s: no org claim or email domain",
            user.pk,
        )
        return

    org_name = entitlements.get("organization_name") or ""

    org, created = get_or_create_organization(external_id, org_name)
    if not created and org_name and org.name != org_name:
        org.name = org_name
        org.save(update_fields=["name", "updated_at"])

    if user.organization_id != org.id:
        logger.info(
            "Moving user %s from organization %s to %s",
            user.pk,
            user.organization_id,
            org.pk,
        )
        with transaction.atomic():
            old_org_id = user.organization_id
            user.organization = org
            user.save(update_fields=["organization", "updated_at"])
            _move_user_data(user, old_org_id, org)


class OIDCAuthenticationBackend(LaSuiteOIDCAuthenticationBackend):
    """Custom OpenID Connect (OIDC) Authentication Backend.

    Extends the La Suite backend with the user's organization: required to
    create a user, and resolved again on every login, when the entitlements
    cache is also refreshed.
    """

    def get_extra_claims(self, user_info):
        """Return extra claims from user_info.

        The org claim (``OIDC_USERINFO_ORGANIZATION_CLAIM``) is surfaced at
        the top level so ``_resolve_org_external_id`` can read it directly,
        independently of whether it's also listed in ``OIDC_STORE_CLAIMS``.
        """
        claims_to_store = {
            claim: user_info.get(claim) for claim in settings.OIDC_STORE_CLAIMS
        }
        extra = {
            "full_name": self.compute_full_name(user_info),
            "claims": claims_to_store,
        }
        org_claim = settings.OIDC_USERINFO_ORGANIZATION_CLAIM
        if org_claim:
            extra[org_claim] = user_info.get(org_claim)
        return extra

    def get_existing_user(self, sub, email):
        """Fetch existing user by sub or email."""
        try:
            return self.UserModel.objects.get_user_by_sub_or_email(sub, email)
        except DuplicateEmailError as err:
            # A concurrent call can create the user of this sub between the
            # lookup by sub and the check of the email. Look up the sub again.
            user = self.UserModel.objects.filter(sub=sub).first()
            if user is None:
                raise SuspiciousOperation(err.message) from err
            return user

    def create_user(self, claims):
        """Create a new user, resolving their organization first.

        Organization is NOT NULL, so we must resolve it before the initial save.
        The org claim is surfaced at the top level of ``claims`` by
        ``get_extra_claims`` so we can read it here. It is left out of what
        ``super().create_user`` gets, which passes every key to ``User(...)``,
        but kept in ``claims``: ``post_get_or_create_user`` reads it again.
        """
        external_id = _resolve_org_external_id(claims)
        if not external_id:
            raise SuspiciousOperation(
                "Cannot create user without an organization "
                "(no org claim and no email domain)"
            )

        org_claim = settings.OIDC_USERINFO_ORGANIZATION_CLAIM
        user_claims = {key: value for key, value in claims.items() if key != org_claim}
        user_claims["organization"], _ = get_or_create_organization(external_id)
        return super().create_user(user_claims)

    def post_get_or_create_user(self, user, claims, is_new_user):
        """Warm the entitlements cache and resolve organization on login.

        A new user also gets the predefined labels.
        """
        if user is None:
            # No match and OIDC_CREATE_USER is off.
            return
        if is_new_user:
            ContactLabel.ensure_defaults(user)
        entitlements = {}
        try:
            # The claims of this login are already stored on the user.
            entitlements = get_user_entitlements(user, force_refresh=True)
        except EntitlementsUnavailableError:
            logger.warning(
                "Entitlements unavailable for %s during login",
                user.email,
            )

        resolve_organization(user, claims, entitlements)
