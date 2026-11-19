"""Admin classes and registrations for the contacts core app."""

from django import forms
from django.contrib import admin, messages
from django.contrib.auth import admin as auth_admin
from django.core.exceptions import ValidationError
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path

from core import models
from core.enums import ChannelScopeLevel, ChannelType


@admin.register(models.Organization)
class OrganizationAdmin(admin.ModelAdmin):
    """Admin class for the Organization model."""

    list_display = ("name", "external_id", "created_at")
    search_fields = ("name", "external_id")


@admin.register(models.User)
class UserAdmin(auth_admin.UserAdmin):
    """Admin class for the User model."""

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "id",
                    "sub",
                    "admin_email",
                    "email",
                    "full_name",
                    "language",
                    "organization",
                    "password",
                )
            },
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        (
            "Important dates",
            {"fields": ("created_at", "updated_at")},
        ),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("admin_email", "password1", "password2"),
            },
        ),
    )
    list_display = (
        "id",
        "sub",
        "admin_email",
        "email",
        "full_name",
        "organization",
        "is_active",
        "is_staff",
        "is_superuser",
        "created_at",
        "updated_at",
    )
    list_filter = ("is_staff", "is_superuser", "is_active")
    list_select_related = ("organization",)
    ordering = (
        "is_active",
        "-is_superuser",
        "-is_staff",
        "-updated_at",
        "full_name",
    )
    # Each login sets the organization from the claims of the identity provider.
    readonly_fields = (
        "id",
        "sub",
        "email",
        "full_name",
        "organization",
        "created_at",
        "updated_at",
    )
    search_fields = ("id", "sub", "admin_email", "email", "full_name")


@admin.register(models.ContactLabel)
class ContactLabelAdmin(admin.ModelAdmin):
    """Admin class for the ContactLabel model."""

    list_display = ("name", "color", "owner")
    list_select_related = ("owner",)
    search_fields = ("name",)
    raw_id_fields = ("owner",)


@admin.register(models.Contact)
class ContactAdmin(admin.ModelAdmin):
    """Admin class for the Contact model."""

    list_display = (
        "display_name",
        "owner",
        "organization",
        "company",
        "source",
        "deleted_at",
        "created_at",
    )
    list_select_related = ("owner", "organization", "overlay_of")
    search_fields = ("full_name", "given_name", "family_name", "search_text")
    raw_id_fields = ("owner", "organization", "overlay_of", "labels", "favorited_by")
    readonly_fields = ("search_text", "sort_key", "created_at", "updated_at")


class ContactGroupMembershipInline(admin.TabularInline):
    """Inline admin for group members."""

    model = models.ContactGroupMembership
    extra = 0
    raw_id_fields = ("contact",)


@admin.register(models.ContactGroup)
class ContactGroupAdmin(admin.ModelAdmin):
    """Admin class for the ContactGroup model."""

    list_display = ("name", "owner", "organization", "created_at")
    list_select_related = ("owner", "organization")
    search_fields = ("name",)
    raw_id_fields = ("owner", "organization", "favorited_by")
    inlines = (ContactGroupMembershipInline,)


@admin.register(models.Channel)
class ChannelAdmin(admin.ModelAdmin):
    """Admin class for the Channel model.

    Global and organization-scoped channels are created here: pick the
    type, scope level and scopes, save, then use "Rotate key" on the
    change page to mint the key (shown once).
    """

    list_display = (
        "name",
        "type",
        "scope_level",
        "user",
        "organization",
        "is_active",
        "last_used_at",
        "created_at",
    )
    list_filter = ("type", "scope_level", "is_active")
    list_select_related = ("user", "organization")
    search_fields = ("name", "user__email")
    raw_id_fields = ("user", "organization")
    readonly_fields = ("last_used_at", "created_at", "updated_at")
    exclude = ("encrypted_settings",)
    change_form_template = "admin/core/channel/change_form.html"

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        """Offer the known values for the free-form type and scope level."""
        choices = {
            "type": ChannelType,
            "scope_level": ChannelScopeLevel,
        }.get(db_field.name)
        if choices is not None:
            kwargs["widget"] = forms.Select(
                choices=[(c.value, c.value) for c in choices]
            )
            return db_field.formfield(**kwargs)
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    def get_urls(self):
        return [
            path(
                "<path:object_id>/rotate-token/",
                self.admin_site.admin_view(self.rotate_token_view),
                name="core_channel_rotate_token",
            ),
            *super().get_urls(),
        ]

    def rotate_token_view(self, request, object_id):
        """Replace the channel's keys with a new one and show it once.

        The key is rendered in this response only, never stored in the
        session or the messages framework.
        """
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        channel = self.get_object(request, object_id)
        if channel is None:
            messages.error(request, "Channel not found.")
            return redirect("admin:core_channel_changelist")
        if not self.has_change_permission(request, channel):
            messages.error(request, "You cannot change this channel.")
            return redirect("admin:core_channel_changelist")
        try:
            api_key = channel.rotate_api_key()
        except ValidationError as exc:
            messages.error(request, f"Fix the channel before rotating: {exc}")
            return redirect("admin:core_channel_change", channel.pk)

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,  # noqa: SLF001  # pylint: disable=protected-access
            "original": channel,
            "title": "New channel key",
            "api_key": api_key,
            "carddav_password": channel.carddav_password(api_key),
        }
        return TemplateResponse(
            request, "admin/core/channel/rotated_token.html", context
        )
