import { baseApiUrl, isJson } from "./utils";
import { APIError } from "./APIError";

/**
 * Retrieves the CSRF token from the document's cookies.
 *
 * @returns {string|undefined} The CSRF token, or undefined if not present.
 */
export function getCSRFToken() {
  return document.cookie
    .split(";")
    .filter((cookie) => cookie.trim().startsWith("csrftoken="))
    .map((cookie) => cookie.split("=")[1])
    .pop();
}

export const SESSION_STORAGE_REDIRECT_AFTER_LOGIN_URL = "redirect_after_login_url";

/**
 * Redirect to the login page on a 401 (expired session), saving the current
 * URL to come back to. Only the first of several concurrent 401s acts, so
 * the saved URL isn't overwritten mid-navigation.
 */
let redirectInFlight = false;
export function redirectToLogin() {
  if (redirectInFlight) return;
  redirectInFlight = true;
  try {
    sessionStorage.setItem(SESSION_STORAGE_REDIRECT_AFTER_LOGIN_URL, window.location.href);
  } catch {
    // Storage unavailable (private browsing, quota): log in anyway.
  }
  window.location.replace(new URL("authenticate/", baseApiUrl()).href);
}

export const fetchAPI = async (
  input: string,
  init?: RequestInit & {
    // Opt out of the default 401 → login redirect. Used by Auth.tsx's boot
    // probe so anonymous pages (homepage, error pages) don't redirect when
    // the user has no session.
    skipAuthRedirect?: boolean;
  },
) => {
  const apiUrl = new URL(`${baseApiUrl("1.0")}${input}`);
  const csrfToken = getCSRFToken();
  const isFormData = init?.body instanceof FormData;

  const response = await fetch(apiUrl, {
    ...init,
    credentials: "include",
    headers: {
      ...init?.headers,
      ...(!isFormData && { "Content-Type": "application/json" }),
      ...(csrfToken && { "X-CSRFToken": csrfToken }),
    },
  });

  if (response.ok) {
    return response;
  }

  if (response.status === 401 && !init?.skipAuthRedirect) {
    redirectToLogin();
  }

  const data = await response.text();

  if (isJson(data)) {
    throw new APIError(response.status, JSON.parse(data));
  }

  throw new APIError(response.status);
};
