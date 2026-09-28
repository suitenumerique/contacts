/**
 * Custom fetch mutator for the orval-generated API client.
 *
 * The generated code passes fully-built relative URLs (path + query,
 * e.g. "/api/v1.0/contacts/?page=2"); this wrapper prefixes the backend
 * origin, attaches session credentials + CSRF, funnels 401s through the
 * login redirect, and returns the `{status, data, headers}` envelope the
 * generated react-query hooks expect.
 *
 * Hand-written calls (task polling, etc.) keep using ./fetchApi.
 */

import { APIError } from "./APIError";
import { baseApiUrl, isJson } from "./utils";
import { getCSRFToken, redirectToLogin } from "./fetchApi";

// https://github.com/orval-labs/orval/issues/258
export type ErrorType<_E> = APIError;

export const fetchAPI = async <T>(url: string, init?: RequestInit): Promise<T> => {
  const requestUrl = new URL(url, baseApiUrl()).href;
  const csrfToken = getCSRFToken();
  const isFormData = init?.body instanceof FormData;

  const response = await fetch(requestUrl, {
    ...init,
    credentials: "include",
    headers: {
      ...init?.headers,
      ...(!isFormData && { "Content-Type": "application/json" }),
      ...(csrfToken && { "X-CSRFToken": csrfToken }),
    },
  });

  if (response.status === 401) {
    redirectToLogin();
  }

  if (response.ok) {
    const data = response.status === 204 ? null : await response.json();
    return { status: response.status, data, headers: response.headers } as T;
  }

  const data = await response.text();
  if (isJson(data)) {
    throw new APIError(response.status, JSON.parse(data));
  }
  throw new APIError(response.status);
};
