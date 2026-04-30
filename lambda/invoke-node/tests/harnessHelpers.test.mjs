import { test } from "node:test";
import assert from "node:assert/strict";
import { historyToHarnessMessages, doubleUuid } from "../harnessHelpers.mjs";

test("doubleUuid produces hex string of at least 33 chars", () => {
  const id = doubleUuid();
  assert.ok(id.length >= 33, `id too short: ${id.length}`);
  assert.match(id, /^[0-9a-f]+$/, "must be hex");
});

test("doubleUuid produces unique ids", () => {
  const a = doubleUuid();
  const b = doubleUuid();
  assert.notEqual(a, b);
});

test("historyToHarnessMessages appends current prompt as final user message", () => {
  const msgs = historyToHarnessMessages(
    [
      { role: "user", content: "hi" },
      { role: "assistant", content: "hello!" },
    ],
    "how are you?"
  );
  assert.equal(msgs.length, 3);
  assert.deepEqual(msgs[0], { role: "user", content: [{ text: "hi" }] });
  assert.deepEqual(msgs[1], { role: "assistant", content: [{ text: "hello!" }] });
  assert.deepEqual(msgs[2], { role: "user", content: [{ text: "how are you?" }] });
});

test("historyToHarnessMessages handles empty history", () => {
  const msgs = historyToHarnessMessages(null, "hi");
  assert.deepEqual(msgs, [{ role: "user", content: [{ text: "hi" }] }]);

  const msgs2 = historyToHarnessMessages([], "hi");
  assert.deepEqual(msgs2, [{ role: "user", content: [{ text: "hi" }] }]);

  const msgs3 = historyToHarnessMessages(undefined, "hi");
  assert.deepEqual(msgs3, [{ role: "user", content: [{ text: "hi" }] }]);
});
