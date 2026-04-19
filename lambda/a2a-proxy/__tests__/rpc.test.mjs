import { test } from "node:test";
import assert from "node:assert/strict";
import { parseRpcRequest, rpcSuccess, rpcError } from "../lib/rpc.mjs";

test("parseRpcRequest accepts message/send", () => {
  const env = {
    jsonrpc: "2.0",
    id: "r1",
    method: "message/send",
    params: { message: { role: "user", parts: [{ kind: "text", text: "hi" }] } },
  };
  const parsed = parseRpcRequest(env);
  assert.equal(parsed.method, "message/send");
  assert.equal(parsed.id, "r1");
});

test("parseRpcRequest rejects invalid jsonrpc version", () => {
  assert.throws(() => parseRpcRequest({ jsonrpc: "1.0", id: "r1", method: "message/send" }), /jsonrpc.*2\.0/);
});

test("parseRpcRequest rejects unsupported method", () => {
  assert.throws(
    () => parseRpcRequest({ jsonrpc: "2.0", id: "r1", method: "tasks/resubscribe", params: {} }),
    /unsupported method/i
  );
});

test("rpcSuccess builds correct envelope", () => {
  const resp = rpcSuccess("r1", { task: { id: "t1", status: { state: "completed" } } });
  assert.equal(resp.jsonrpc, "2.0");
  assert.equal(resp.id, "r1");
  assert.ok(resp.result);
  assert.equal(resp.error, undefined);
});

test("rpcError uses -32601 for method not found", () => {
  const err = rpcError("r1", -32601, "Method not found");
  assert.equal(err.error.code, -32601);
  assert.equal(err.result, undefined);
});
