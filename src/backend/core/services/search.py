"""People and group suggestions for other Suite apps (``/external_api/``).

The method, before the details: the response has ``limit`` places, filled
from four queues in priority order. Every queue only holds rows matching
the query, and the database hands each one over already sorted:

1. rows the user picked before (``/interactions/``), by decayed rank;
2. rows the user starred, alphabetical;
3. rows where a word *starts with* the query, alphabetical;
4. rows with the query *inside* a word (words of 3+ characters only),
   alphabetical.

Queues 1 and 2 accept a match anywhere in a word: they are small and
personal, so a weaker match is fine. Queue 3 keeps word starts apart from
queue 4 because, across a whole directory, a word start ("mar": Martin) is
far more likely what the user means than a match inside a word ("mar":
Dumarais).

Each queue is asked only for the places still missing, skips the rows
already placed, and the search stops as soon as every place is filled. It
is exact (a queue that returns fewer rows than asked for has no more, and a
later queue only holds lower-priority rows) and fast (no query reads more
than a few dozen rows, whatever the size of the address book). See
docs/resource_server.md, "Performance".

"Matching" means every word of the query is found in the row's
``search_text``: names, nickname, company, department, emails and phone
numbers, not the job title, notes or addresses
(``core.utils.build_search_text``). So "mai" also finds everyone whose
company is "Mairie de Brigny".
"""

from dataclasses import dataclass

from django.db.models import Count, Exists, F, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce, Lower

from core import models
from core.enums import Scope
from core.phones import phone_query_digits
from core.utils import fold

CONTACT = "contact"
GROUP = "group"
KINDS = (CONTACT, GROUP)

# Interactions considered for ranking, most recent first. A pick older
# than a year weighs under 1/4000 of a fresh one with the default half-life.
MAX_RANKED = 1000
# Extra rows every step asks for. The user's own card is never suggested;
# it is dropped in Python (``_keep``), since matching it in SQL means
# comparing normalized addresses inside JSON lists, inherited by overlays.
# Without the margin, a dropped card would leave a step one row short, and
# the runner would wrongly take that step for exhausted. 3 covers the
# user's synced directory entry plus a duplicate or two in their own book.
SELF_MARGIN = 3
# Words shorter than this only match the start of a word: the trigram
# index can't serve "anywhere" below three characters (see ``anywhere``).
MIN_ANYWHERE_LENGTH = 3


@dataclass(frozen=True)
class Query:
    """A search query, folded and split as ``search_text`` is stored."""

    words: tuple

    @classmethod
    def parse(cls, text):
        """Parse *text*; a phone number is one run of digits."""
        if digits := phone_query_digits(text or ""):
            return cls((digits,))
        return cls(tuple(fold(text).split()))

    def __bool__(self):
        return bool(self.words)

    @property
    def can_match_inside(self):
        """Whether step 4 can find anything step 3 didn't."""
        return any(len(word) >= MIN_ANYWHERE_LENGTH for word in self.words)

    def word_start(self):
        """Every word of the query starts a word of the row."""
        condition = Q()
        for word in self.words:
            condition &= Q(search_text__contains=f" {word}")
        return condition

    def anywhere(self):
        """Every word matches: inside a word from 3 characters, else a start."""
        condition = Q()
        for word in self.words:
            # The trigram index stores 3-character pieces, padded with spaces
            # at word edges ("mairie": "  m", " ma", "mai", ...). "%ma%" gives
            # no full trigram, so Postgres would read the whole index, while
            # " ma" hits a word-start trigram: short words match starts only.
            if len(word) >= MIN_ANYWHERE_LENGTH:
                condition &= Q(search_text__contains=word)
            else:
                condition &= Q(search_text__contains=f" {word}")
        return condition


def _has_email(personal):
    """Rows with at least one address; an overlay may read its parent's."""
    own = Q(emails__0__isnull=False)
    if not personal:
        return own
    inherited = Q(overlay_of__emails__0__isnull=False) & ~Q(
        overridden_fields__contains=["emails"]
    )
    return own | inherited


# Columns a result needs (ContactResultSerializer, display_name, the self
# filter); the rest (notes, vCard extras, addresses, search_text...) stays
# in the database.
CONTACT_FIELDS = (
    "id",
    "organization_id",
    "owner_id",
    "scope",
    "source",
    "overlay_of_id",
    "overridden_fields",
    "full_name",
    "given_name",
    "middle_name",
    "family_name",
    "name_suffix",
    "emails",
    "company",
    "job_title",
    "photo",
    "sort_key",
)
GROUP_FIELDS = ("id", "organization_id", "owner_id", "scope", "source", "name", "email")


def contact_books(user):
    """The user's searchable contacts, as two querysets: personal, organization.

    Kept apart so each walks the ``(organization, owner, sort_key)`` index
    on its own: an OR of both books can't be read in order from one index.
    Trash is left out, an overlaid row gives way to the user's overlay, and
    contacts without any email are left out (consumers invite by email).
    """
    rows = models.Contact.objects.filter(
        organization_id=user.organization_id, deleted_at__isnull=True
    )
    personal = (
        rows.filter(scope=Scope.PERSONAL, owner=user)
        .exclude(overlays__owner=user)
        .filter(_has_email(personal=True))
        .select_related("overlay_of")
        .only(*CONTACT_FIELDS, *(f"overlay_of__{field}" for field in CONTACT_FIELDS))
    )
    organization = (
        rows.filter(scope=Scope.ORGANIZATION, owner__isnull=True)
        .exclude(overlays__owner=user)
        .filter(_has_email(personal=False))
        .only(*CONTACT_FIELDS)
    )
    return personal, organization


def group_book(user):
    """The groups the user may see."""
    return models.ContactGroup.objects.visible_to(user).only(*GROUP_FIELDS)


def with_members_count(groups, user):
    """Annotate ``members_count``: visible, non-trashed members.

    An overlay and its parent are one logical contact. Only apply it to the
    few groups a response returns: it counts every member.
    """
    visible = models.Contact.objects.visible_to(user)
    return groups.annotate(
        members_count=Count(
            Coalesce("members__overlay_of_id", "members__id"),
            filter=Q(members__deleted_at__isnull=True, members__in=visible),
            distinct=True,
        )
    )


def _sort_key(kind, row):
    return row.sort_key if kind == CONTACT else row.name.lower()


class _Search:
    """One search: the steps, what they found, what is left to find."""

    def __init__(self, user, query, kinds, limit):
        self.user = user
        self.query = query
        self.kinds = kinds
        self.limit = limit
        self.results = []  # (kind, row), in order
        self.taken = {CONTACT: set(), GROUP: set()}
        self.own_email = (user.email or "").strip().lower()

    @property
    def missing(self):
        """How many more rows the response needs."""
        return self.limit - len(self.results)

    def _is_self(self, kind, row):
        return kind == CONTACT and self.own_email in row.email_values

    def _keep(self, rows):
        """Add *rows* (kind, row) in order, until every place is filled.

        Skips the user's own card and rows already placed by an earlier step
        (``_untaken`` excludes those in SQL already; this is the backstop).
        """
        for kind, row in rows:
            if len(self.results) >= self.limit:
                return
            if self._is_self(kind, row) or row.pk in self.taken[kind]:
                continue
            self.taken[kind].add(row.pk)
            self.results.append((kind, row))

    def _untaken(self, queryset, kind):
        """*queryset* without the rows already placed (a person takes one place)."""
        if self.taken[kind]:
            return queryset.exclude(pk__in=self.taken[kind])
        return queryset

    def _alphabetical(self, condition_for):
        """Rows matching a condition, alphabetical, at most ``missing``.

        Shared by steps 2, 3 and 4. *condition_for(kind)* gives the filter
        for each kind. The personal book, the organization book and the
        groups are queried apart, each read in order and stopped after
        ``size`` rows, then the three short lists are merged.
        """
        size = self.missing + SELF_MARGIN
        rows = []
        if CONTACT in self.kinds:
            for book in contact_books(self.user):
                matches = self._untaken(book.filter(condition_for(CONTACT)), CONTACT)
                rows += [
                    (CONTACT, row) for row in matches.order_by("sort_key", "pk")[:size]
                ]
        if GROUP in self.kinds:
            groups = self._untaken(group_book(self.user), GROUP)
            groups = groups.filter(condition_for(GROUP)).order_by(Lower("name"), "pk")
            rows += [(GROUP, row) for row in groups[:size]]
        # Merge the three lists by name; the pk only orders equal names.
        rows.sort(key=lambda item: (_sort_key(*item), str(item[1].pk)))
        self._keep(rows)

    def ranked(self):
        """Step 1: rows matching the query that the user picked, by rank.

        The rank is computed in SQL, so only the rows the response needs
        are loaded, however many the user ever picked.
        """
        size = self.missing + SELF_MARGIN
        interactions = models.Interaction.objects.filter(user=self.user)
        # A pick ranks a row higher; it never makes a row match. An empty
        # query has no filter: the "people you share with" list.
        match = self.query.anywhere() if self.query else Q()

        def ranked_rows(queryset, kind, target, *, overlays=False):
            """*queryset* rows the user picked, with their rank, best first."""
            field = f"{kind}_id"
            # Never loaded in Python: a sliced queryset stays lazy, and
            # Django inlines it as a subquery, IN (SELECT ... LIMIT 1000).
            recent = (
                interactions.filter(**{f"{field}__isnull": False})
                .order_by("-last_interaction_at")
                .values(field)[:MAX_RANKED]
            )
            rank = interactions.filter(**{field: target}).with_rank().values("rank")[:1]
            picked = Q(pk__in=recent)
            if overlays:
                # The user's overlay of a picked row (picks are stored on
                # the row it covers).
                picked |= Q(overlay_of_id__in=recent)
            queryset = self._untaken(queryset.filter(match), kind)
            return [
                (row.rank, kind, row)
                for row in queryset.filter(picked)
                .annotate(rank=Subquery(rank))
                .order_by(F("rank").desc(nulls_last=True), "pk")[:size]
            ]

        rows = []
        if CONTACT in self.kinds:
            personal, organization = contact_books(self.user)
            rows += ranked_rows(
                personal,
                CONTACT,
                Coalesce(OuterRef("overlay_of_id"), OuterRef("pk")),
                overlays=True,
            )
            rows += ranked_rows(organization, CONTACT, OuterRef("pk"))
        if GROUP in self.kinds:
            rows += ranked_rows(group_book(self.user), GROUP, OuterRef("pk"))
        # Merge the three lists, each already sorted by rank. sort() is
        # ascending, so the rank is negated to put the highest first; the
        # name then orders equal ranks A to Z (reverse=True would turn the
        # names around too).
        rows.sort(key=lambda item: (-item[0], _sort_key(item[1], item[2])))
        self._keep((kind, row) for _rank, kind, row in rows)

    def favorites(self):
        """Step 2: rows matching the query that the user starred."""
        user = self.user
        starred_contact = models.Contact.favorited_by.through.objects.filter(
            user_id=user.pk
        )
        starred_group = models.ContactGroup.favorited_by.through.objects.filter(
            user_id=user.pk
        )

        def condition(kind):
            if kind == CONTACT:
                return self.query.anywhere() & (
                    Exists(starred_contact.filter(contact_id=OuterRef("pk")))
                    | Exists(
                        starred_contact.filter(contact_id=OuterRef("overlay_of_id"))
                    )
                )
            return self.query.anywhere() & Exists(
                starred_group.filter(contactgroup_id=OuterRef("pk"))
            )

        self._alphabetical(condition)

    def word_starts(self):
        """Step 3: everyone else where a word starts with the query.

        The only step limited to word starts: across the whole directory,
        they come before matches inside a word (step 4).
        """
        self._alphabetical(lambda kind: self.query.word_start())

    def inside_words(self):
        """Step 4: everyone else with the query inside a word.

        Only run when a word has 3+ characters (``can_match_inside``):
        below that, ``anywhere()`` is a word start and would repeat step 3.
        """
        self._alphabetical(lambda kind: self.query.anywhere())

    def run(self):
        """Run the steps until the response is full."""
        steps = [self.ranked]
        if self.query:
            steps += [self.favorites, self.word_starts]
            if self.query.can_match_inside:
                steps.append(self.inside_words)
        for step in steps:
            if self.missing <= 0:
                break  # every place is filled: later steps can't rank higher
            step()
        return self.results


def search(user, text="", *, kinds=KINDS, limit=10):
    """The contacts and groups suggested to *user* for *text*, in order.

    Returns ``(kind, row)`` pairs; groups carry ``members_count``. An empty
    *text* returns only what the user interacted with, by rank: the "people
    you share with" list a share modal shows when it opens.
    """
    results = _Search(user, Query.parse(text), tuple(kinds), limit).run()
    group_ids = [row.pk for kind, row in results if kind == GROUP]
    if group_ids:
        counts = dict(
            with_members_count(
                models.ContactGroup.objects.filter(pk__in=group_ids), user
            ).values_list("pk", "members_count")
        )
        for kind, row in results:
            if kind == GROUP:
                row.members_count = counts.get(row.pk, 0)
    return results
