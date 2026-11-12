/**
 * Thin adapters over the generated react-query client: they unwrap the
 * `{status, data}` envelopes, flatten infinite pages and centralize cache
 * invalidation.
 */

import { useMemo } from "react";
import { keepPreviousData, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  contactsImportCsvCreate,
  contactsImportVcfCreate,
  useContactsCreate,
  useContactsDestroy,
  useContactsFavoriteCreate,
  useContactsFavoriteDestroy,
  useContactsForkCreate,
  useContactsListInfinite,
  useContactsPartialUpdate,
  useContactsRestoreCreate,
  useContactsRetrieve,
  useContactsShareCreate,
} from "@/features/api/gen/contacts/contacts";
import {
  useGroupsAddContactCreate,
  useGroupsContactsList,
  useGroupsCreate,
  useGroupsDestroy,
  useGroupsFavoriteCreate,
  useGroupsFavoriteDestroy,
  useGroupsList,
  useGroupsPartialUpdate,
  useGroupsRemoveContactCreate,
} from "@/features/api/gen/groups/groups";
import {
  useLabelsCreate,
  useLabelsDestroy,
  useLabelsList,
  useLabelsPartialUpdate,
} from "@/features/api/gen/labels/labels";
import {
  useChannelsCreate,
  useChannelsDestroy,
  useChannelsList,
} from "@/features/api/gen/channels/channels";
import type { ContactsListParams, ScopeEnum } from "@/features/api/gen/models";
import { pollImportTask } from "../api";
import type { ContactFilters } from "../types";

const PAGE_SIZE = 50;

const listParams = (filters: ContactFilters): ContactsListParams => {
  const params: ContactsListParams = { page_size: PAGE_SIZE };
  if (filters.q) params.q = filters.q;
  if (filters.label) params.label = filters.label;
  if (filters.group) params.group = filters.group;
  if (filters.favorites) params.favorites = true;
  if (filters.trash) params.trash = true;
  if (filters.sortDesc) params.sort = "desc";
  return params;
};

/** Infinite contact list for the given filters. */
export const useContactsList = (filters: ContactFilters, { enabled = true } = {}) => {
  const result = useContactsListInfinite(listParams(filters), {
    query: {
      enabled,
      // A search keeps the previous rows until the new ones arrive.
      // The list does not blank out between keystrokes.
      placeholderData: filters.q ? keepPreviousData : undefined,
      initialPageParam: 1,
      getNextPageParam: (lastPage) => {
        const next = lastPage.data.next;
        if (!next) return undefined;
        const page = new URL(next, window.location.origin).searchParams.get("page");
        return page ? Number(page) : undefined;
      },
    },
  });

  const contacts = useMemo(
    () => (result.data?.pages ?? []).flatMap((page) => page.data.results),
    [result.data],
  );

  const count = result.data?.pages?.[0]?.data.count ?? contacts.length;

  return { ...result, contacts, count };
};

export const useContact = (id: string | undefined) => {
  const query = useContactsRetrieve(id ?? "", { query: { enabled: !!id } });
  return { ...query, contact: query.data?.data ?? null };
};

export const useGroups = () => {
  const query = useGroupsList();
  return { ...query, groups: query.data?.data ?? [] };
};

export const useLabels = () => {
  const query = useLabelsList();
  return { ...query, labels: query.data?.data ?? [] };
};

export const useGroupContacts = (groupId: string | undefined) => {
  const query = useGroupsContactsList(groupId ?? "", {
    query: { enabled: !!groupId },
  });
  return { ...query, contacts: query.data?.data ?? [] };
};

/** Orval keys plain queries as ["/api/v1.0/contacts/", …] but infinite ones
 *  as ["infinite", "/api/v1.0/contacts/", …]: match the URL anywhere. */
const INVALIDATE_PREFIXES = [
  "/api/v1.0/contacts/",
  "/api/v1.0/groups/",
  "/api/v1.0/labels/",
  "/api/v1.0/channels/",
];

const useInvalidate = () => {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({
      predicate: (query) =>
        query.queryKey.some(
          (part) =>
            typeof part === "string" &&
            INVALIDATE_PREFIXES.some((prefix) => part.startsWith(prefix)),
        ),
    });
  };
};

const withInvalidation = <T extends (options: { mutation: { onSuccess: () => void } }) => unknown>(
  useGeneratedMutation: T,
) =>
  function useMutationWithInvalidation() {
    const invalidate = useInvalidate();
    return useGeneratedMutation({ mutation: { onSuccess: invalidate } }) as ReturnType<T>;
  };

export const useCreateContact = withInvalidation(useContactsCreate);
export const useUpdateContact = withInvalidation(useContactsPartialUpdate);
export const useDeleteContact = withInvalidation(useContactsDestroy);
export const useRestoreContact = withInvalidation(useContactsRestoreCreate);
/** Create (or fetch) a personal, editable overlay of a read-only contact. */
export const useForkContact = withInvalidation(useContactsForkCreate);
/** Copy one of the user's contacts into the organization's address book. */
export const useShareContact = withInvalidation(useContactsShareCreate);
export const useFavoriteContact = withInvalidation(useContactsFavoriteCreate);
export const useUnfavoriteContact = withInvalidation(useContactsFavoriteDestroy);

export const useCreateGroup = withInvalidation(useGroupsCreate);
export const useUpdateGroup = withInvalidation(useGroupsPartialUpdate);
export const useDeleteGroup = withInvalidation(useGroupsDestroy);
export const useAddContactToGroup = withInvalidation(useGroupsAddContactCreate);
export const useRemoveContactFromGroup = withInvalidation(useGroupsRemoveContactCreate);
export const useFavoriteGroup = withInvalidation(useGroupsFavoriteCreate);
export const useUnfavoriteGroup = withInvalidation(useGroupsFavoriteDestroy);

export const useCreateLabel = withInvalidation(useLabelsCreate);
export const useUpdateLabel = withInvalidation(useLabelsPartialUpdate);
export const useDeleteLabel = withInvalidation(useLabelsDestroy);

export const useCarddavChannels = () => {
  const query = useChannelsList();
  const channels = useMemo(
    () => (query.data?.data ?? []).filter((channel) => channel.type === "carddav"),
    [query.data],
  );
  return { ...query, channels };
};
export const useCreateChannel = withInvalidation(useChannelsCreate);
export const useDeleteChannel = withInvalidation(useChannelsDestroy);

/**
 * Upload a .vcf or .csv file and poll the import task to completion, into
 * the user's address book or (administrators) their organization's.
 */
export const useImportContacts = () => {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async ({ file, scope }: { file: File; scope: ScopeEnum }) => {
      const isCsv = /\.csv$/i.test(file.name) || file.type === "text/csv";
      const upload = isCsv ? contactsImportCsvCreate : contactsImportVcfCreate;
      const response = await upload({ file, scope });
      return pollImportTask(response.data.task_id);
    },
    onSuccess: invalidate,
  });
};
