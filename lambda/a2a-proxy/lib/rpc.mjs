export const SUPPORTED_METHODS = new Set(["message/send", "message/stream"]);

export function parseRpcRequest(env) {
  if (!env || env.jsonrpc !== "2.0") throw new Error("jsonrpc must be '2.0'");
  if (env.id === undefined || env.id === null) throw new Error("missing request id");
  if (!env.method || typeof env.method !== "string") throw new Error("missing method");
  if (!SUPPORTED_METHODS.has(env.method)) throw new Error(`unsupported method: ${env.method}`);
  return { jsonrpc: "2.0", id: env.id, method: env.method, params: env.params || {} };
}

export function rpcSuccess(id, result) {
  return { jsonrpc: "2.0", id, result };
}

export function rpcError(id, code, message, data) {
  const err = { code, message };
  if (data !== undefined) err.data = data;
  return { jsonrpc: "2.0", id, error: err };
}
