/**
 * Types for the contacts feature. Entity shapes come from the generated
 * client (src/features/api/gen); only UI-level types live here.
 */

export type {
  Channel,
  ChannelWithKey,
  Contact,
  ContactList as ContactListItem,
  ContactRequest as ContactPayload,
  ContactGroup,
  ContactLabel,
  TaskStatus as ImportTaskStatus,
  TypedValue,
} from "@/features/api/gen/models";

/** Server-side filters of the contact list. */
export interface ContactFilters {
  q?: string;
  label?: string;
  group?: string;
  favorites?: boolean;
  trash?: boolean;
  sortDesc?: boolean;
}
