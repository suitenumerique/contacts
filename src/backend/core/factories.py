"""
Core application factories
"""

from django.conf import settings
from django.contrib.auth.hashers import make_password

import factory.fuzzy
from faker import Faker

from core import models
from core.enums import ChannelScopeLevel, ChannelType

fake = Faker()


class OrganizationFactory(factory.django.DjangoModelFactory):
    """A factory to create organizations for testing purposes."""

    class Meta:
        model = models.Organization

    name = factory.Faker("company")
    external_id = factory.Sequence(lambda n: f"org-{n}")


class UserFactory(factory.django.DjangoModelFactory):
    """A factory to random users for testing purposes."""

    class Meta:
        model = models.User
        skip_postgeneration_save = True

    sub = factory.Sequence(lambda n: f"user{n!s}")
    email = factory.Faker("email")
    full_name = factory.Faker("name")
    language = factory.fuzzy.FuzzyChoice([lang[0] for lang in settings.LANGUAGES])
    password = make_password("password")
    organization = factory.SubFactory(OrganizationFactory)


class ContactFactory(factory.django.DjangoModelFactory):
    """A factory to create personal contacts for testing purposes."""

    class Meta:
        model = models.Contact

    owner = factory.SubFactory(UserFactory)
    organization = factory.LazyAttribute(lambda obj: obj.owner.organization)
    full_name = factory.Faker("name")
    emails = factory.LazyFunction(lambda: [{"type": "work", "value": fake.email()}])


class ContactGroupFactory(factory.django.DjangoModelFactory):
    """A factory to create contact groups for testing purposes."""

    class Meta:
        model = models.ContactGroup

    owner = factory.SubFactory(UserFactory)
    organization = factory.LazyAttribute(lambda obj: obj.owner.organization)
    name = factory.Faker("word")


class ChannelFactory(factory.django.DjangoModelFactory):
    """A factory to create channels for testing purposes."""

    class Meta:
        model = models.Channel
        skip_postgeneration_save = True

    name = factory.Faker("sentence", nb_words=3)
    type = ChannelType.API_KEY.value
    scope_level = ChannelScopeLevel.USER.value
    user = factory.SubFactory(UserFactory)
    organization = factory.LazyAttribute(
        lambda obj: obj.user.organization if obj.user else None
    )
    settings = factory.LazyFunction(lambda: {"scopes": ["contacts:read"]})

    @factory.post_generation
    def api_key(obj, create, extracted, **kwargs):  # pylint: disable=unused-argument,no-self-argument
        """Generate an API key and expose the raw value as ``obj.raw_api_key``."""
        # pylint: disable=attribute-defined-outside-init
        if not create:
            return
        obj.raw_api_key = obj.generate_api_key()
        obj.save()
