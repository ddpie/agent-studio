import { test } from "node:test";
import assert from "node:assert/strict";
import crypto from "node:crypto";
import { hashKey, extractBearerToken } from "../lib/auth.mjs";

test("hashKey matches sha256 hex", () => {
  const k = "as_abcdef";
  const expected = crypto.createHash("sha256").update(k).digest("hex");
  assert.equal(hashKey(k), expected);
});

test("extractBearerToken returns trimmed token", () => {
  assert.equal(extractBearerToken("Bearer abc123"), "abc123");
  assert.equal(extractBearerToken("bearer abc"), "abc");
  assert.equal(extractBearerToken(""), null);
  assert.equal(extractBearerToken("Basic xyz"), null);
  assert.equal(extractBearerToken(null), null);
});
