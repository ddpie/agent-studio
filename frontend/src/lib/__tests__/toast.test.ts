import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    loading: vi.fn(() => "id-123"),
    dismiss: vi.fn(),
  },
}));

import { toast as sonnerToast } from "sonner";
import { toast } from "../toast";

describe("toast wrapper", () => {
  beforeEach(() => vi.clearAllMocks());

  it("success forwards to sonner.success with description", () => {
    toast.success("Saved", { description: "Your agent was saved." });
    expect(sonnerToast.success).toHaveBeenCalledWith("Saved", {
      description: "Your agent was saved.",
    });
  });

  it("error with Error instance pulls .message", () => {
    toast.error(new Error("boom"));
    expect(sonnerToast.error).toHaveBeenCalledWith("boom", undefined);
  });

  it("error with string", () => {
    toast.error("oops");
    expect(sonnerToast.error).toHaveBeenCalledWith("oops", undefined);
  });

  it("loading returns a dismissible id", () => {
    const id = toast.loading("Deploying...");
    expect(sonnerToast.loading).toHaveBeenCalledWith("Deploying...", undefined);
    toast.dismiss(id);
    expect(sonnerToast.dismiss).toHaveBeenCalledWith("id-123");
  });
});
