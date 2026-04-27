/**
 * Translate native AgentCore harness stream events into Agent Studio's
 * __tool SSE protocol.
 *
 * MVP mapping:
 *   contentBlockDelta{delta.text}            -> data: "<text>"\n\n
 *   internalServerException/runtimeClientError -> data: {"__error":"..."}\n\n
 *   messageStart/messageStop/metadata        -> dropped
 *   contentBlockStart/contentBlockStop       -> dropped (tool use out of scope)
 *
 * Future extension (when harness MVP grows tool support):
 *   contentBlockStart{toolUse}               -> {"__tool":"start","name":...}
 *   contentBlockDelta{delta.toolUse.input}   -> accumulate
 *   contentBlockStop + toolResult event       -> {"__tool":"result"...}
 */
export async function* translateHarnessStream(harnessStream) {
  for await (const event of harnessStream) {
    if (event.contentBlockDelta) {
      const text = event.contentBlockDelta?.delta?.text;
      if (typeof text === "string" && text.length > 0) {
        yield `data: ${JSON.stringify(text)}\n\n`;
      }
      continue;
    }
    if (event.internalServerException) {
      const msg = event.internalServerException.message || "internal server error";
      yield `data: ${JSON.stringify({ __error: msg })}\n\n`;
      continue;
    }
    if (event.runtimeClientError) {
      const msg = event.runtimeClientError.message || "runtime client error";
      yield `data: ${JSON.stringify({ __error: msg })}\n\n`;
      continue;
    }
    // messageStart / messageStop / metadata / contentBlockStart / contentBlockStop — drop.
  }
}
