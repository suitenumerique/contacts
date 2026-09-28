/* eslint-disable @typescript-eslint/no-explicit-any */
import i18n from "@/features/i18n/initI18n";

export class APIError extends Error {
  data?: any;
  code: number;

  constructor(code: number, data?: any) {
    super();
    this.data = data;
    this.code = code;
  }
}

export const errorToString = (error: unknown): string => {
  if (typeof error === "string") {
    return error;
  }

  if (error instanceof APIError) {
    const data = error.data;
    // A plain-text body is shown as is.
    if (typeof data === "string") {
      return data;
    }

    if (data?.errors && Array.isArray(data?.errors) && data?.errors?.length > 0) {
      return data.errors[0].detail;
    }

    if (data) {
      /**
       * This is made to handle full text errors from the API like:
       *
       * "title": [
       *   "The title field is required."
       *  ]
       */
      return Object.entries(error.data)
        .map(([, value]) => `${value}`)
        .join("\n");
    }
    // If there is no data, it means that the error is a string, probably a complicated html error.
    return i18n.t("api.error.unexpected");
  }
  // Other errors' messages are too technical to show the user.
  return i18n.t("api.error.unexpected");
};
