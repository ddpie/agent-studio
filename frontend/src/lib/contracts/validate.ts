import { type ZodSchema, type ZodError } from "zod";

/**
 * Validate an API response against a Zod schema.
 *
 * - In development mode, logs a warning when validation fails but still
 *   returns the data (to avoid breaking the UI during iteration).
 * - In test mode, throws so contract tests can catch mismatches.
 * - In production, silently passes through (no runtime cost).
 *
 * @param schema - Zod schema to validate against
 * @param data - The raw API response payload
 * @param label - Optional label for the log message (e.g. endpoint name)
 * @returns The validated (parsed) data of type T
 * @throws ZodError in test environments when validation fails
 */
export function validateResponse<T>(
  schema: ZodSchema<T>,
  data: unknown,
  label?: string,
): T {
  const result = schema.safeParse(data);

  if (result.success) {
    return result.data;
  }

  const prefix = label ? `[Contract: ${label}]` : "[Contract]";
  const error: ZodError = result.error;

  // In test environment, throw so tests can assert on contract violations
  if (import.meta.env?.MODE === "test" || typeof process !== "undefined" && process.env?.NODE_ENV === "test") {
    throw error;
  }

  // In development, warn but don't break
  if (import.meta.env?.DEV) {
    console.warn(
      `${prefix} API response does not match contract:`,
      error.issues.map((i) => `${i.path.join(".")}: ${i.message}`),
    );
  }

  // Return the data as-is (unsafe cast) so the app doesn't break
  return data as T;
}
