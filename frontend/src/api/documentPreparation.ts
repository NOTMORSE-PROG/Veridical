import { ApiError } from "./client";

// F1/F2/F7: only this envelope promises rejection before durable upload work.
export function isDocumentPreparationBusy(error: unknown): error is ApiError {
  return error instanceof ApiError
    && error.status === 503
    && error.code === "document_preparation_busy";
}
