"""API endpoints"""

import functools
import json
import logging
import uuid

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.template.defaultfilters import filesizeformat
from django.utils import timezone
from django.utils.text import slugify

from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import exceptions as drf_exceptions
from rest_framework import mixins, pagination, response, status, views, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny

from core import models
from core.authentication.channel import resolve_acting_user
from core.entitlements import user_can_admin
from core.enums import ChannelScope, Scope
from core.phones import phone_query_digits
from core.utils import strip_diacritics

from . import permissions, serializers

logger = logging.getLogger(__name__)


# pylint: disable=too-many-ancestors


class SerializerPerActionMixin:
    """
    A mixin to allow to define serializer classes for each action.

    This mixin is useful to avoid to define a serializer class for each action in the
    `get_serializer_class` method.

    Example:
    ```
    class MyViewSet(SerializerPerActionMixin, viewsets.GenericViewSet):
        serializer_class = MySerializer
        list_serializer_class = MyListSerializer
        retrieve_serializer_class = MyRetrieveSerializer
    ```
    """

    def get_serializer_class(self):
        """
        Return the serializer class to use depending on the action.
        """
        if serializer_class := getattr(self, f"{self.action}_serializer_class", None):
            return serializer_class
        return super().get_serializer_class()


class Pagination(pagination.PageNumberPagination):
    """Page-number pagination; ``page_size`` is capped at MAX_PAGE_SIZE."""

    max_page_size = settings.MAX_PAGE_SIZE
    page_size_query_param = "page_size"


class UserViewSet(
    SerializerPerActionMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """User ViewSet: the current user's profile only."""

    permission_classes = [permissions.IsSelf]
    queryset = models.User.objects.filter(is_active=True)
    serializer_class = serializers.UserSerializer
    get_me_serializer_class = serializers.UserMeSerializer

    @action(
        detail=False,
        methods=["get"],
        url_name="me",
        url_path="me",
    )
    def get_me(self, request):
        """
        Return information on currently logged user
        """
        context = {"request": request}
        return response.Response(
            self.get_serializer(request.user, context=context).data
        )


class ConfigView(views.APIView):
    """API ViewSet for sharing some public settings."""

    permission_classes = [AllowAny]

    @extend_schema(responses=serializers.ConfigSerializer)
    def get(self, request):
        """
        GET /api/v1.0/config/
            Return a dictionary of public settings.
        """
        config = {
            "FRONTEND_THEME": settings.FRONTEND_THEME,
            "FRONTEND_MORE_LINK": settings.FRONTEND_MORE_LINK,
            "FRONTEND_TERMS_OF_SERVICE_URL": settings.FRONTEND_TERMS_OF_SERVICE_URL,
            "FRONTEND_FEEDBACK_BUTTON_SHOW": settings.FRONTEND_FEEDBACK_BUTTON_SHOW,
            "FRONTEND_FEEDBACK_BUTTON_IDLE": settings.FRONTEND_FEEDBACK_BUTTON_IDLE,
            "FRONTEND_FEEDBACK_ITEMS": settings.FRONTEND_FEEDBACK_ITEMS,
            "FRONTEND_FEEDBACK_MESSAGES_WIDGET_ENABLED": (
                settings.FRONTEND_FEEDBACK_MESSAGES_WIDGET_ENABLED
            ),
            "FRONTEND_FEEDBACK_MESSAGES_WIDGET_API_URL": (
                settings.FRONTEND_FEEDBACK_MESSAGES_WIDGET_API_URL
            ),
            "FRONTEND_FEEDBACK_MESSAGES_WIDGET_CHANNEL": (
                settings.FRONTEND_FEEDBACK_MESSAGES_WIDGET_CHANNEL
            ),
            "FRONTEND_FEEDBACK_MESSAGES_WIDGET_PATH": (
                settings.FRONTEND_FEEDBACK_MESSAGES_WIDGET_PATH
            ),
            "FRONTEND_LAGAUFRE_ENABLED": settings.FRONTEND_LAGAUFRE_ENABLED,
            "FRONTEND_LAGAUFRE_WIDGET_PATH": settings.FRONTEND_LAGAUFRE_WIDGET_PATH,
            "FRONTEND_LAGAUFRE_WIDGET_API_URL": settings.FRONTEND_LAGAUFRE_WIDGET_API_URL,
            "FRONTEND_MESSAGES_COMPOSE_URL": settings.FRONTEND_MESSAGES_COMPOSE_URL,
            "TRASHBIN_CUTOFF_DAYS": settings.TRASHBIN_CUTOFF_DAYS,
            "FEATURE_CARDDAV": settings.FEATURE_CARDDAV,
            "LANGUAGES": settings.LANGUAGES,
            "LANGUAGE_CODE": settings.LANGUAGE_CODE,
            "theme_customization": self._load_theme_customization(),
        }
        return response.Response(serializers.ConfigSerializer(config).data)

    def _load_theme_customization(self):
        if not settings.THEME_CUSTOMIZATION_FILE_PATH:
            return {}

        cache_key = (
            f"theme_customization_{slugify(settings.THEME_CUSTOMIZATION_FILE_PATH)}"
        )
        theme_customization = cache.get(cache_key, {})
        if theme_customization:
            return theme_customization

        try:
            with open(
                settings.THEME_CUSTOMIZATION_FILE_PATH, "r", encoding="utf-8"
            ) as f:
                theme_customization = json.load(f)
        except FileNotFoundError:
            logger.error(
                "Configuration file not found: %s",
                settings.THEME_CUSTOMIZATION_FILE_PATH,
            )
        except json.JSONDecodeError:
            logger.error(
                "Configuration file is not a valid JSON: %s",
                settings.THEME_CUSTOMIZATION_FILE_PATH,
            )
        else:
            cache.set(
                cache_key,
                theme_customization,
                settings.THEME_CUSTOMIZATION_CACHE_TIMEOUT,
            )

        return theme_customization


def _search_filter(field, query):
    """Per-word containment filter on an accent-folded stored *field*.

    The column is stored lowercased and accent-folded (Contact.clean) and
    the query is folded the same way, so a case-sensitive ``contains``
    (``LIKE``) is enough and stays covered by the trigram index, which
    ``icontains`` (``UPPER(...) LIKE UPPER(...)``) is not. A query that is a
    phone number is looked up as one run of digits (see ``core.phones``).
    """
    if digits := phone_query_digits(query):
        return Q(**{f"{field}__contains": digits})
    filters = Q()
    for word in strip_diacritics(query.lower()).split():
        filters &= Q(**{f"{field}__contains": word})
    return filters


def _get_or_404(queryset, pk, message):
    """Fetch *pk* from *queryset*, answering 404 for unknown or malformed ids."""
    try:
        return queryset.get(pk=pk)
    except (queryset.model.DoesNotExist, DjangoValidationError, ValueError) as exc:
        raise drf_exceptions.NotFound(message) from exc


def _import_file_error(file_format: str, data: bytes) -> str:
    """Why the upload cannot be a *file_format* file, or "" when it can.

    The checks use the content only. The client chooses the file name and
    the Content-Type of the upload, and a valid export can have any of them.
    """
    # pylint: disable-next=import-outside-toplevel
    from core.services.vcf_service import contains_vcard  # noqa: PLC0415

    if not data.strip():
        return "The file is empty."
    # The parsers read UTF-8 and Windows-1252 text. Such text has no NUL byte.
    if b"\0" in data:
        return "The file is not a text file."
    if file_format == "vcf" and not contains_vcard(data):
        return "The file contains no vCard."
    return ""


def visible_contacts(user):
    """Every contact row *user* may see, trashed ones included.

    Their own rows plus their organization's, with what the serializers
    read for *user* prefetched: visible groups (also of an overlay's
    parent), the user's labels, and whether the user starred the row or
    the row it overlays. The organization's trash is its administrators'.
    """
    groups = serializers.visible_groups(user).only("id", "name")
    rows = models.Contact.objects.visible_to(user)
    if not user_can_admin(user):
        rows = rows.exclude(scope=Scope.ORGANIZATION, deleted_at__isnull=False)
    return (
        rows.select_related("overlay_of")
        .prefetch_related(
            Prefetch("groups", queryset=groups, to_attr="visible_groups"),
            Prefetch("overlay_of__groups", queryset=groups, to_attr="visible_groups"),
            Prefetch("labels", queryset=models.ContactLabel.objects.filter(owner=user)),
        )
        .annotate(
            is_favorite=Exists(
                models.Contact.favorited_by.through.objects.filter(
                    Q(contact_id=OuterRef("pk"))
                    | Q(contact_id=OuterRef("overlay_of_id")),
                    user_id=user.pk,
                )
            )
        )
    )


class ActingUserMixin:
    """The user a request acts for, resolved once per request."""

    @functools.cached_property
    def acting_user(self):
        """The session user, or the channel's user / on_behalf_of user."""
        return resolve_acting_user(self.request)

    @property
    def is_channel_request(self):
        """Whether the request is authenticated by a service channel."""
        return isinstance(self.request.auth, models.Channel)

    def get_serializer_context(self):
        """Pass the acting user on to the serializers."""
        context = super().get_serializer_context()
        context["acting_user"] = self.acting_user
        return context


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter("q", str, description="Search text (name, email, org)."),
            OpenApiParameter("favorites", bool, description="Only favorites."),
            OpenApiParameter("group", str, description="Filter by group id."),
            OpenApiParameter("label", str, description="Filter by label id."),
            OpenApiParameter("trash", bool, description="List the corbeille instead."),
            OpenApiParameter(
                "sort",
                str,
                enum=["asc", "desc"],
                description="Alphabetical direction (default asc).",
            ),
        ]
    )
)
class ContactViewSet(ActingUserMixin, SerializerPerActionMixin, viewsets.ModelViewSet):
    """CRUD for contacts: the user's own, and their organization's.

    Visible rows are the acting user's personal contacts plus their
    organization's shared ones (see ``Contact.objects.visible_to``). Who
    may write a row is ``Contact.is_writable``: its owner, or for an
    organization contact the organization's administrators.

    Read endpoints also accept Suite service channels (X-Channel-Id +
    X-API-Key headers) holding the ``contacts:read`` scope, acting on
    behalf of a user (``on_behalf_of`` parameter). Channels never see the
    corbeille.
    """

    # Only tells the schema generator the model; get_queryset scopes rows.
    queryset = models.Contact.objects.none()
    serializer_class = serializers.ContactSerializer
    list_serializer_class = serializers.ContactListSerializer
    pagination_class = Pagination

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [
                (
                    permissions.IsEntitledToAccess
                    | permissions.channel_scope(ChannelScope.CONTACTS_READ.value)
                )()
            ]
        return [permissions.IsEntitledToAccess()]

    def get_queryset(self):
        user = self.acting_user
        queryset = visible_contacts(user)
        if self.is_channel_request:
            queryset = queryset.filter(deleted_at__isnull=True)

        if self.action != "list":
            return queryset

        # In the list, a read-only contact the user has overlaid is hidden
        # in favour of its overlay, so a single merged entry is shown.
        queryset = queryset.exclude(overlays__owner=user)

        params = self.request.query_params
        if params.get("trash") == "true" and not self.is_channel_request:
            # The user's own trashed contacts, and for an administrator the
            # organization's (visible_contacts hides them from others).
            return queryset.filter(
                Q(owner=user) | Q(scope=Scope.ORGANIZATION), deleted_at__isnull=False
            ).order_by("-deleted_at")
        queryset = queryset.filter(deleted_at__isnull=True)

        if query := params.get("q", "").strip():
            queryset = queryset.filter(_search_filter("search_text", query))

        if params.get("favorites") == "true":
            queryset = queryset.filter(is_favorite=True)
        if group_id := params.get("group"):
            group = _get_or_404(
                serializers.visible_groups(user), group_id, "Group not found."
            )
            # Memberships live on canonical rows, so an overlay is in the
            # group through its parent.
            queryset = queryset.filter(
                Q(groups=group) | Q(overlay_of__groups=group)
            ).distinct()
        if label_id := params.get("label"):
            label = _get_or_404(
                models.ContactLabel.objects.filter(owner=user),
                label_id,
                "Label not found.",
            )
            queryset = queryset.filter(labels=label)

        # Sorting server-side: the client cannot reverse the list itself
        # without holding every page, which is not an option on a real
        # address book.
        if params.get("sort") == "desc":
            return queryset.order_by("-sort_key", "-created_at")
        return queryset.order_by("sort_key", "created_at")

    def _check_writable(self, contact):
        """Refuse a write the acting user may not make (``is_writable``).

        Synced rows are read-only; a personal row (overlays included) is
        its owner's; an organization row is its administrators'.
        """
        if not contact.is_writable(self.acting_user):
            self.permission_denied(
                self.request,
                message="This contact is read-only; edit a local copy instead.",
            )

    def _serialize(self, contact):
        return serializers.ContactSerializer(
            contact, context=self.get_serializer_context()
        ).data

    @extend_schema(request=None, responses=serializers.ContactSerializer)
    @action(detail=True, methods=["post"], url_path="fork")
    def fork(self, request, pk=None):  # pylint: disable=unused-argument
        """Create (or return) the acting user's local overlay of a contact.

        Forking a read-only contact (org-directory or synced) yields a
        personal, editable overlay merged on top of it: the user can rename
        or change fields locally while untouched fields keep tracking the
        source. Returns the existing overlay if there is one.
        """
        user = self.acting_user
        contact = self.get_object()
        if contact.overlay_of_id is not None or contact.is_writable(user):
            raise drf_exceptions.ValidationError(
                "This contact is already editable; no local copy is needed."
            )
        existing = models.Contact.objects.filter(owner=user, overlay_of=contact)
        overlay = existing.first()
        if overlay is None:
            try:
                with transaction.atomic():
                    overlay = models.Contact.objects.create(
                        owner=user,
                        overlay_of=contact,
                        organization=user.organization,
                        overridden_fields=[],
                    )
            except IntegrityError, DjangoValidationError:
                # A concurrent fork won the race.
                overlay = existing.first()
                if overlay is None:
                    raise
        return response.Response(
            self._serialize(self.get_queryset().get(pk=overlay.pk)),
            status=status.HTTP_200_OK,
        )

    @extend_schema(request=None, responses={204: None})
    @action(detail=True, methods=["post", "delete"], url_path="favorite")
    def favorite(self, request, pk=None):  # pylint: disable=unused-argument
        """Star (POST) or unstar (DELETE) the contact for the acting user.

        Allowed on read-only contacts: the star is per user. An overlay's
        star is stored on its parent, so reverting the overlay keeps it.
        """
        contact = self.get_object()
        user = self.acting_user
        if request.method == "POST":
            (contact.overlay_of or contact).favorited_by.add(user)
        else:
            contact.favorited_by.remove(user)
            if contact.overlay_of_id:
                contact.overlay_of.favorited_by.remove(user)
        return response.Response(status=status.HTTP_204_NO_CONTENT)

    # Contact writes run in a transaction so that the post_save receivers
    # (the CardDAV change journal, overlay search fields) commit with them
    # or not at all.

    def perform_create(self, serializer):
        if serializer.validated_data.get("is_org_contact") and not user_can_admin(
            self.acting_user
        ):
            self.permission_denied(
                self.request,
                message="Only an administrator can create an organization contact.",
            )
        with transaction.atomic():
            serializer.save()

    def perform_update(self, serializer):
        self._check_writable(serializer.instance)
        if serializer.instance.deleted_at is not None:
            raise drf_exceptions.ValidationError(
                "This contact is in the trash; restore it before you edit it."
            )
        with transaction.atomic():
            serializer.save()

    @extend_schema(request=None, responses={201: serializers.ContactSerializer})
    @action(detail=True, methods=["post"], url_path="share")
    def share(self, request, pk=None):  # pylint: disable=unused-argument
        """Copy one of the user's contacts into the organization's address book.

        Administrators only. See ``Contact.share_with_organization``: notes
        and labels stay private, and the user's contact either goes or
        remains as a local copy holding them. Returns the shared contact.
        """
        user = self.acting_user
        contact = self.get_object()
        if not user_can_admin(user):
            self.permission_denied(
                request, message="Only an administrator can share a contact."
            )
        if (
            not contact.is_writable(user)
            or contact.is_org_contact
            or contact.overlay_of_id
            or contact.deleted_at is not None
        ):
            raise drf_exceptions.ValidationError(
                "Only one of your own contacts can be shared."
            )
        with transaction.atomic():
            shared = contact.share_with_organization(user)
        return response.Response(
            self._serialize(self.get_queryset().get(pk=shared.pk)),
            status=status.HTTP_201_CREATED,
        )

    @transaction.atomic
    def perform_destroy(self, instance):
        """First delete moves to the corbeille; second one is permanent.

        Deleting an overlay just drops the local edits (revert to source),
        so it is removed outright rather than sent to the corbeille.
        """
        self._check_writable(instance)
        if instance.overlay_of_id is not None:
            instance.delete()
        elif instance.deleted_at is None:
            instance.deleted_at = timezone.now()
            instance.updated_by = self.acting_user
            instance.save(update_fields=["deleted_at", "updated_by", "updated_at"])
        else:
            # Contact.delete detaches the users' overlays of the row.
            instance.delete()

    @extend_schema(request=None, responses=serializers.ContactSerializer)
    @action(detail=True, methods=["post"], url_path="restore")
    def restore(self, request, pk=None):  # pylint: disable=unused-argument
        """Restore a contact from the corbeille."""
        contact = self.get_object()
        self._check_writable(contact)
        if contact.deleted_at is not None:
            contact.deleted_at = None
            with transaction.atomic():
                contact.save(update_fields=["deleted_at", "updated_at"])
        return response.Response(self._serialize(contact))

    def _queue_import(self, request, file_format):
        """Validate the uploaded file and queue the import task.

        The file goes to the cache for the worker to pick up, not into the
        task message itself.
        """
        # pylint: disable-next=import-outside-toplevel
        from core.tasks import IMPORT_FILE_TTL, import_contacts_task  # noqa: PLC0415

        if "file" not in request.FILES:
            return response.Response(
                {"detail": "No file provided"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        scope = request.data.get("scope") or Scope.PERSONAL.value
        if scope not in {value.value for value in Scope}:
            return response.Response(
                {"scope": f"Unknown scope: {scope}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if scope == Scope.ORGANIZATION and not user_can_admin(request.user):
            self.permission_denied(
                request,
                message="Only an administrator can import into the organization.",
            )

        uploaded_file = request.FILES["file"]
        max_size = settings.CONTACTS_VCF_MAX_FILE_SIZE
        if uploaded_file.size > max_size:
            return response.Response(
                {
                    "detail": "File too large. Maximum size is "
                    f"{filesizeformat(max_size)}."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = uploaded_file.read()
        if error := _import_file_error(file_format, data):
            return response.Response(
                {"detail": error}, status=status.HTTP_400_BAD_REQUEST
            )

        file_key = f"import_file:{uuid.uuid4()}"
        cache.set(file_key, data, timeout=IMPORT_FILE_TTL)
        task = import_contacts_task.delay(
            str(request.user.id), file_format, file_key, scope
        )
        task.track_owner(request.user.id)

        return response.Response(
            {"task_id": task.id},
            status=status.HTTP_202_ACCEPTED,
        )

    @extend_schema(
        request=serializers.FileUploadSerializer,
        responses={202: serializers.TaskQueuedSerializer},
    )
    @action(
        detail=False,
        methods=["post"],
        parser_classes=[MultiPartParser],
        url_path="import-vcf",
        url_name="import-vcf",
    )
    def import_vcf(self, request, **kwargs):
        """Import contacts from a vCard (.vcf) file.

        POST /api/v1.0/contacts/import-vcf/  (multipart: file=<vcf>)

        Returns a task_id to poll at GET /api/v1.0/tasks/{task_id}/.
        """
        return self._queue_import(request, "vcf")

    @extend_schema(
        request=serializers.FileUploadSerializer,
        responses={202: serializers.TaskQueuedSerializer},
    )
    @action(
        detail=False,
        methods=["post"],
        parser_classes=[MultiPartParser],
        url_path="import-csv",
        url_name="import-csv",
    )
    def import_csv(self, request, **kwargs):
        """Import contacts from a CSV file (see docs for the column model).

        POST /api/v1.0/contacts/import-csv/  (multipart: file=<csv>)
        """
        return self._queue_import(request, "csv")

    def _exported_contacts(self, request):
        """The user's own non-trashed contacts, overlays included."""
        return (
            models.Contact.objects.filter(owner=request.user, deleted_at__isnull=True)
            .select_related("overlay_of")
            .prefetch_related("labels")
            .order_by("sort_key")
        )

    @action(
        detail=False,
        methods=["get"],
        url_path="export-vcf",
        url_name="export-vcf",
    )
    def export_vcf(self, request, **kwargs):
        """Export the user's personal contacts as a vCard (.vcf) file."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.vcf_service import serialize_contacts  # noqa: PLC0415

        payload = serialize_contacts(self._exported_contacts(request))
        http_response = HttpResponse(payload, content_type="text/vcard")
        http_response["Content-Disposition"] = 'attachment; filename="contacts.vcf"'
        return http_response

    @action(
        detail=False,
        methods=["get"],
        url_path="export-csv",
        url_name="export-csv",
    )
    def export_csv(self, request, **kwargs):
        """Export the user's personal contacts as a CSV file."""
        # pylint: disable-next=import-outside-toplevel
        from core.services.csv_service import serialize_contacts_csv  # noqa: PLC0415

        payload = serialize_contacts_csv(self._exported_contacts(request))
        http_response = HttpResponse(payload, content_type="text/csv")
        http_response["Content-Disposition"] = 'attachment; filename="contacts.csv"'
        return http_response


class ContactLabelViewSet(viewsets.ModelViewSet):
    """CRUD for the user's labels (libellés).

    The predefined set (DEFAULT_LABELS) is seeded when the user is created;
    users manage their own labels freely.
    """

    queryset = models.ContactLabel.objects.none()
    serializer_class = serializers.ContactLabelSerializer
    permission_classes = [permissions.IsEntitledToAccess]
    pagination_class = None

    def get_queryset(self):
        return (
            models.ContactLabel.objects.filter(owner=self.request.user)
            .annotate(
                contacts_count=Count(
                    "contacts",
                    filter=Q(contacts__deleted_at__isnull=True),
                )
            )
            .order_by("name")
        )

    def perform_create(self, serializer):
        _save_or_name_clash(serializer, "You already have a label with this name.")

    def perform_update(self, serializer):
        _save_or_name_clash(serializer, "You already have a label with this name.")


def _save_or_name_clash(serializer, message):
    """Save, turning a concurrent unique-name violation into a 400."""
    try:
        with transaction.atomic():
            serializer.save()
    except IntegrityError as exc:
        raise drf_exceptions.ValidationError({"name": [message]}) from exc


class ContactGroupViewSet(ActingUserMixin, viewsets.ModelViewSet):
    """CRUD for contact groups + membership management.

    Groups follow the same visibility rules as contacts: personal groups
    plus org-wide groups (owner NULL), which only the organization's
    administrators may create and edit (see ``ContactGroup.is_writable``).
    """

    queryset = models.ContactGroup.objects.none()
    serializer_class = serializers.ContactGroupSerializer
    pagination_class = None

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [
                (
                    permissions.IsEntitledToAccess
                    | permissions.channel_scope(ChannelScope.GROUPS_READ.value)
                )()
            ]
        return [permissions.IsEntitledToAccess()]

    def _visible_members(self):
        """Filter on ``members`` matching the rows the acting user may see."""
        user = self.acting_user
        return Q(
            members__deleted_at__isnull=True,
            members__in=models.Contact.objects.visible_to(user),
        )

    def get_queryset(self):
        user = self.acting_user
        return (
            serializers.visible_groups(user)
            .annotate(
                # An overlay and its parent are one logical contact.
                members_count=Count(
                    Coalesce("members__overlay_of_id", "members__id"),
                    filter=self._visible_members(),
                    distinct=True,
                ),
                is_favorite=Exists(
                    models.ContactGroup.favorited_by.through.objects.filter(
                        contactgroup_id=OuterRef("pk"), user_id=user.pk
                    )
                ),
            )
            .order_by("name")
        )

    def _check_writable(self, group):
        """Refuse a write the acting user may not make (``is_writable``)."""
        if not group.is_writable(self.acting_user):
            self.permission_denied(self.request, message="This group is read-only.")

    def perform_create(self, serializer):
        if serializer.validated_data.get("is_org_group") and not user_can_admin(
            self.acting_user
        ):
            self.permission_denied(
                self.request,
                message="Only an administrator can create a shared group.",
            )
        _save_or_name_clash(serializer, "You already have a group with this name.")

    def perform_update(self, serializer):
        self._check_writable(serializer.instance)
        _save_or_name_clash(serializer, "You already have a group with this name.")

    def perform_destroy(self, instance):
        self._check_writable(instance)
        instance.delete()

    @extend_schema(responses=serializers.ContactListSerializer(many=True))
    @action(detail=True, methods=["get"], url_path="contacts")
    def list_contacts(self, request, pk=None):  # pylint: disable=unused-argument
        """List the contacts belonging to this group.

        Members the acting user has overlaid are represented by their
        overlay (merged), matching the main list's "one entry" rule. Only
        members the user may see (own rows, org-wide rows) are listed: a
        provisioned org-wide group may hold other users' personal rows.
        """
        user = self.acting_user
        group = self.get_object()
        contacts = visible_contacts(user).filter(deleted_at__isnull=True)
        members = contacts.exclude(overlays__owner=user).filter(groups=group)
        # Overlays of members; an overlay that is itself a member (only the
        # admin can store one) is already listed.
        overlays = contacts.filter(
            owner=user, overlay_of__memberships__group=group
        ).exclude(memberships__group=group)
        rows = sorted(
            [*members, *overlays], key=lambda contact: (contact.sort_key, contact.pk)
        )
        serializer = serializers.ContactListSerializer(
            rows, many=True, context=self.get_serializer_context()
        )
        return response.Response(serializer.data)

    @extend_schema(request=None, responses={204: None})
    @action(detail=True, methods=["post", "delete"], url_path="favorite")
    def favorite(self, request, pk=None):  # pylint: disable=unused-argument
        """Star (POST) or unstar (DELETE) the group for the acting user.

        Allowed on read-only groups too: the star is per user, not a
        change to the shared row.
        """
        group = self.get_object()
        if request.method == "POST":
            group.favorited_by.add(self.acting_user)
        else:
            group.favorited_by.remove(self.acting_user)
        return response.Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=serializers.GroupMemberSerializer, responses={204: None})
    @action(detail=True, methods=["post"], url_path="add-contact")
    def add_contact(self, request, pk=None):  # pylint: disable=unused-argument
        """Add a contact to the group. Body: {"contact_id": <uuid>}.

        An overlay is added through its parent, the canonical row. A shared
        group only takes contacts the whole organization sees: a personal
        contact would be an invisible member to everyone else.
        """
        group = self.get_object()
        self._check_writable(group)
        contact = self._get_member_candidate(request)
        canonical = contact.overlay_of or contact
        if group.is_org_group and canonical.owner_id is not None:
            raise drf_exceptions.ValidationError(
                {
                    "contact_id": (
                        "Only contacts shared with the organization can join "
                        "a shared group."
                    )
                }
            )
        try:
            with transaction.atomic():
                models.ContactGroupMembership.objects.get_or_create(
                    group=group, contact=canonical
                )
        except IntegrityError, DjangoValidationError:
            # Added concurrently: the membership exists, which is the goal.
            pass
        return response.Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=serializers.GroupMemberSerializer, responses={204: None})
    @action(detail=True, methods=["post"], url_path="remove-contact")
    def remove_contact(self, request, pk=None):  # pylint: disable=unused-argument
        """Remove a contact from the group. Body: {"contact_id": <uuid>}.

        Given an overlay, removes its parent's membership and its own.
        """
        group = self.get_object()
        self._check_writable(group)
        contact = self._get_member_candidate(request)
        rows = [contact.pk]
        if contact.overlay_of_id:
            rows.append(contact.overlay_of_id)
        models.ContactGroupMembership.objects.filter(
            group=group, contact_id__in=rows
        ).delete()
        return response.Response(status=status.HTTP_204_NO_CONTENT)

    def _get_member_candidate(self, request):
        """Resolve the contact named by the request body, within visibility.

        Trashed contacts are hidden from the roster, so they cannot be
        added to (or removed from) a group either.
        """
        payload = serializers.GroupMemberSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        user = self.acting_user
        return _get_or_404(
            models.Contact.objects.visible_to(user)
            .select_related("overlay_of")
            .filter(deleted_at__isnull=True),
            payload.validated_data["contact_id"],
            "Contact not found.",
        )
