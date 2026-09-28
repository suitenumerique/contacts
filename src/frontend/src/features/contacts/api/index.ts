/**
 * Import-task polling, which the generated client doesn't cover.
 */

import { fetchAPI } from "@/features/api/fetchApi";
import type { ImportTaskStatus } from "../types";

const POLL_INTERVAL_MS = 1000;
const POLL_TIMEOUT_MS = 15 * 60 * 1000;

export const getTaskStatus = async (taskId: string): Promise<ImportTaskStatus> => {
  const response = await fetchAPI(`tasks/${taskId}/`);
  return (await response.json()) as ImportTaskStatus;
};

/** Poll an import task until it succeeds, fails, or the timeout expires. */
export const pollImportTask = async (taskId: string): Promise<ImportTaskStatus> => {
  const deadline = Date.now() + POLL_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const status = await getTaskStatus(taskId);
    if (status.status === "SUCCESS" || status.status === "FAILURE") {
      return status;
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
  }
  return { status: "FAILURE", error: "timeout", result: null };
};
