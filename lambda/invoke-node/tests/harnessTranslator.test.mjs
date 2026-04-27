import { test } from "node:test";
import assert from "node:assert/strict";
import { translateHarnessStream } from "../harnessTranslator.mjs";

async function collect(asyncIter) {
  const out = [];
  for await (const chunk of asyncIter) out.push(chunk);
  return out;
}

async function* fromArray(arr) {
  for (const x of arr) yield x;
}

test("emits text deltas as __tool-compatible SSE", async () => {
  const events = [
    { messageStart: { role: "assistant" } },
    { contentBlockDelta: { delta: { text: "Hello" } } },
    { contentBlockDelta: { delta: { text: " world" } } },
    { messageStop: { stopReason: "end_turn" } },
  ];
  const out = await collect(translateHarnessStream(fromArray(events)));
  assert.deepEqual(out, [
    'data: "Hello"\n\n',
    'data: " world"\n\n',
  ]);
});

test("drops metadata events in MVP", async () => {
  const events = [
    { contentBlockDelta: { delta: { text: "hi" } } },
    { metadata: { usage: { inputTokens: 5, outputTokens: 2 } } },
  ];
  const out = await collect(translateHarnessStream(fromArray(events)));
  assert.deepEqual(out, ['data: "hi"\n\n']);
});

test("maps internalServerException to __error", async () => {
  const events = [{ internalServerException: { message: "boom" } }];
  const out = await collect(translateHarnessStream(fromArray(events)));
  assert.equal(out.length, 1);
  assert.ok(out[0].includes('"__error"'));
  assert.ok(out[0].includes("boom"));
});

test("maps runtimeClientError to __error", async () => {
  const events = [{ runtimeClientError: { message: "bad request" } }];
  const out = await collect(translateHarnessStream(fromArray(events)));
  assert.equal(out.length, 1);
  assert.ok(out[0].includes('"__error"'));
  assert.ok(out[0].includes("bad request"));
});

test("skips contentBlockDelta without text", async () => {
  const events = [
    { contentBlockDelta: { delta: { toolUse: { input: "{}" } } } },
    { contentBlockDelta: { delta: { text: "ok" } } },
  ];
  const out = await collect(translateHarnessStream(fromArray(events)));
  // MVP: only text deltas emit; toolUse deltas ignored (MVP has no tools).
  assert.deepEqual(out, ['data: "ok"\n\n']);
});

test("escapes double quotes and newlines in text", async () => {
  const events = [
    { contentBlockDelta: { delta: { text: 'hi "there"\nagain' } } },
  ];
  const out = await collect(translateHarnessStream(fromArray(events)));
  assert.equal(out[0], 'data: "hi \\"there\\"\\nagain"\n\n');
});
