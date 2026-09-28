/**
 * URL search (de)serialization for the router. The default one JSON-encodes
 * values, and TanStack's own decoder turns "1234" or "true" into a number or
 * a boolean: search params stay plain strings here, as the API expects.
 */

type Search = Record<string, unknown>;

export const parseSearch = (searchStr: string): Search =>
  Object.fromEntries(new URLSearchParams(searchStr));

export const stringifySearch = (search: Search): string => {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(search)) {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  }
  const query = params.toString();
  return query ? `?${query}` : "";
};
