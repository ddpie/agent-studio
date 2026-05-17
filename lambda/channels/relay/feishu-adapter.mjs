/**
 * Feishu Channel Adapter — implements the ChannelAdapter interface.
 *
 * Connects via @larksuiteoapi/node-sdk WSClient to receive Feishu events
 * over a persistent WebSocket (outbound, no public endpoint needed).
 *
 * Handles:
 *   - im.message.receive_v1 → InboundMessage (type: "message")
 *   - card.action.trigger → InboundCardAction (type: "card_action")
 *   - im.chat.member.bot.added_v1 → InboundBotEvent (event: "bot_added")
 *   - im.chat.member.bot.deleted_v1 → InboundBotEvent (event: "bot_removed")
 */

import * as lark from "@larksuiteoapi/node-sdk";

export class FeishuAdapter {
  /** @type {"feishu"} */
  platformType = "feishu";

  /** @type {boolean} */
  connected = false;

  /** @type {string} */
  #channelId;

  /** @type {string} */
  #workspaceId;

  /** @type {lark.WSClient|null} */
  #wsClient = null;

  /** @type {((msg: object) => Promise<void>)|null} */
  #eventCallback = null;

  /** @type {lark.Client|null} */
  #apiClient = null;

  /** @type {string} */
  #appId = "";

  /** @type {string} */
  #appSecret = "";

  /**
   * @param {string} channelId
   * @param {string} workspaceId
   */
  constructor(channelId, workspaceId) {
    this.#channelId = channelId;
    this.#workspaceId = workspaceId;
  }

  /**
   * Register event callback. Must be called before connect().
   * @param {(msg: object) => Promise<void>} callback
   */
  onEvent(callback) {
    this.#eventCallback = callback;
  }

  /**
   * Connect to Feishu via WebSocket.
   * @param {{ appId: string; botName?: string; domain?: string }} platformConfig
   * @param {{ appSecret: string }} secret
   */
  async connect(platformConfig, secret) {
    this.#appId = platformConfig.appId;
    this.#appSecret = secret.appSecret;

    // Create API client for catch-up calls
    this.#apiClient = new lark.Client({
      appId: this.#appId,
      appSecret: this.#appSecret,
      domain: platformConfig.domain === "lark" ? lark.Domain.Lark : lark.Domain.Feishu,
    });

    // Create event dispatcher with handlers (SDK register() takes an object map)
    const eventDispatcher = new lark.EventDispatcher({});
    eventDispatcher.register({
      "im.message.receive_v1": (data) => this.#handleMessage(data),
      "card.action.trigger": (data) => this.#handleCardAction(data),
      "im.chat.member.bot.added_v1": (data) => this.#handleBotAdded(data),
      "im.chat.member.bot.deleted_v1": (data) => this.#handleBotRemoved(data),
    });

    // Create WebSocket client
    this.#wsClient = new lark.WSClient({
      appId: this.#appId,
      appSecret: this.#appSecret,
      loggerLevel: lark.LoggerLevel.WARN,
    });

    await this.#wsClient.start({ eventDispatcher });
    this.connected = true;

    console.log(`[FeishuAdapter] Connected: channelId=${this.#channelId}`);
  }

  /**
   * Disconnect from Feishu WebSocket.
   * WSClient does not expose a public close/stop method. We mark as
   * disconnected so any in-flight event callbacks are discarded, then
   * null the reference for GC. The underlying WS connection will
   * terminate when the process exits or the object is collected.
   */
  async disconnect() {
    this.connected = false;
    this.#wsClient = null;
    this.#apiClient = null;
    console.log(`[FeishuAdapter] Disconnected: channelId=${this.#channelId}`);
  }

  /**
   * After reconnect, fetch recent messages since the given timestamp.
   * Uses Feishu's im/v1/messages API (limited to what the bot can see).
   * Note: Feishu does not provide a general "messages since X" API for bots;
   * this is a best-effort catch-up using the chat history endpoint.
   * @param {number} _since — epoch ms (unused in V1, placeholder for future)
   * @returns {Promise<object[]>}
   */
  async catchUp(_since) {
    // V1: Feishu WebSocket SDK handles reconnection and message delivery
    // internally. Missed messages during brief disconnects are re-delivered
    // by the platform. Full catch-up would require per-chat polling which
    // is not feasible at scale. Return empty for now.
    return [];
  }

  /**
   * Handle im.message.receive_v1 event.
   * @param {object} data — Feishu event payload
   */
  async #handleMessage(data) {
    if (!this.#eventCallback || !this.connected) return;

    const event = data;
    const message = event.message;
    if (!message) return;

    // Parse message content (JSON-encoded string)
    let content = "";
    let contentType = "text";
    try {
      const parsed = JSON.parse(message.content || "{}");
      content = parsed.text || "";
      contentType = "text";
    } catch {
      content = message.content || "";
    }

    // Extract user info
    const userId = event.sender?.sender_id?.open_id || "";
    const userName = event.sender?.sender_id?.name || "";

    /** @type {object} */
    const inboundMessage = {
      type: "message",
      channelId: this.#channelId,
      workspaceId: this.#workspaceId,
      chatId: message.chat_id || "",
      chatType: message.chat_type === "p2p" ? "p2p" : "group",
      threadId: message.thread_id || undefined,
      messageId: message.message_id || "",
      userId,
      userName,
      content,
      contentType,
      mentions: message.mentions || [],
      timestamp: Date.now(),
    };

    try {
      await this.#eventCallback(inboundMessage);
    } catch (err) {
      console.error(`[FeishuAdapter] Error in message callback:`, err);
    }
  }

  /**
   * Handle card.action.trigger event (user clicked a card button).
   * @param {object} data — Feishu card action payload
   */
  async #handleCardAction(data) {
    if (!this.#eventCallback || !this.connected) return;

    console.log("[FeishuAdapter] card_action raw:", JSON.stringify(data).slice(0, 800));

    const action = data.action;
    const operator = data.operator;

    const chatId = data.context?.open_chat_id || "";
    // Feishu group chat IDs start with "oc_", P2P starts with other prefixes
    const chatType = chatId.startsWith("oc_") ? "group" : "p2p";

    const inboundCardAction = {
      type: "card_action",
      channelId: this.#channelId,
      workspaceId: this.#workspaceId,
      chatId,
      chatType,
      userId: operator?.open_id || "",
      action: {
        agentId: action?.value?.agentId || "",
        cardToken: data.token || "",
        messageId: data.context?.open_message_id || "",
      },
      timestamp: Date.now(),
    };

    try {
      await this.#eventCallback(inboundCardAction);
    } catch (err) {
      console.error(`[FeishuAdapter] Error in card_action callback:`, err);
    }
  }

  /**
   * Handle im.chat.member.bot.added_v1 event.
   * @param {object} data — Feishu bot added event
   */
  async #handleBotAdded(data) {
    if (!this.#eventCallback || !this.connected) return;

    const inboundBotEvent = {
      type: "bot_event",
      channelId: this.#channelId,
      workspaceId: this.#workspaceId,
      event: "bot_added",
      chatId: data.chat_id || "",
      chatName: data.name || undefined,
      timestamp: Date.now(),
    };

    try {
      await this.#eventCallback(inboundBotEvent);
    } catch (err) {
      console.error(`[FeishuAdapter] Error in bot_added callback:`, err);
    }
  }

  /**
   * Handle im.chat.member.bot.deleted_v1 event.
   * @param {object} data — Feishu bot removed event
   */
  async #handleBotRemoved(data) {
    if (!this.#eventCallback || !this.connected) return;

    const inboundBotEvent = {
      type: "bot_event",
      channelId: this.#channelId,
      workspaceId: this.#workspaceId,
      event: "bot_removed",
      chatId: data.chat_id || "",
      chatName: data.name || undefined,
      timestamp: Date.now(),
    };

    try {
      await this.#eventCallback(inboundBotEvent);
    } catch (err) {
      console.error(`[FeishuAdapter] Error in bot_removed callback:`, err);
    }
  }
}
