import { useState, useCallback } from "react";
import type { ValidationResult } from "../lib/types/validation";

export default function useAsyncValidation() {
  const [validating, setValidating] = useState(false);
  const [result, setResult] = useState<ValidationResult | null>(null);

  const validate = useCallback(async (fn: () => Promise<ValidationResult>) => {
    setValidating(true);
    setResult(null);
    try {
      const res = await fn();
      setResult(res);
      if (res.valid && res.errors.length === 0 && res.warnings.length === 0) {
        setTimeout(() => setResult(null), 2000);
      }
    } catch {
      setResult({ valid: false, errors: ["Validation failed unexpectedly"], warnings: [] });
    } finally {
      setValidating(false);
    }
  }, []);

  return { validating, result, setResult, validate };
}
