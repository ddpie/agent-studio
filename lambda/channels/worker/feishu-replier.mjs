/**
 * Feishu Replier — CardKit streaming implementation.
 *
 * Implements the ChannelReplier interface for Feishu:
 * - Create streaming cards
 * - Write thinking placeholder
 * - Append streamed content (prefix-append model)
 * - Finalize (close streaming mode)
 * - Send selection cards and fallback messages
 */

const FEISHU_BASE = "https://open.feishu.cn";

const I18N = {
  en: {
    thinking: "Thinking...",
    respondingAs: (name) => `Responding as: ${name}`,
    switchAssistant: "Switch Assistant",
    selectPrompt: "Which assistant would you like to chat with?",
    selectedConfirm: (name) => `Got it! You're now chatting with ${name}. Send me your question!`,
    errorGeneric: "Something went wrong. Please try again.",
    truncated: "[Response truncated due to timeout]",
  },
  zh: {
    thinking: "思考中...",
    respondingAs: (name) => `当前助手: ${name}`,
    switchAssistant: "切换助手",
    selectPrompt: "你想和哪位助手对话？",
    selectedConfirm: (name) => `好的！你现在正在和「${name}」对话。请发送你的问题！`,
    errorGeneric: "出了点问题，请重试。",
    truncated: "[回复因超时被截断]",
  },
};

function t(lang, key, ...args) {
  const strings = I18N[lang] || I18N.en;
  const val = strings[key];
  return typeof val === "function" ? val(...args) : val;
}

/**
 * @typedef {Object} StreamContext
 * @property {string} cardId
 * @property {string} chatId
 * @property {string} agentName
 * @property {number} sequence
 * @property {boolean} isPrivateChat
 */

export class FeishuReplier {
  #lang;

  constructor(lang = "zh") {
    this.#lang = lang;
  }

  get supportsStreaming() {
    return true;
  }

  /**
   * Create a streaming card and send it to the chat.
   *
   * @param {string} chatId
   * @param {string} accessToken
   * @param {{agentName: string, isPrivateChat: boolean}} opts
   * @returns {Promise<StreamContext>}
   */
  async createStreamingReply(chatId, accessToken, opts = {}) {
    const { agentName = "Assistant", isPrivateChat = false } = opts;

    // Step 1: Create card with streaming_mode
    const cardData = {
      schema: "2.0",
      config: { wide_screen_mode: true, streaming_mode: true },
      body: {
        elements: [
          { tag: "markdown", content: "", element_id: "stream_el" },
        ],
      },
    };

    // Footer: always show agent identity; private chat also gets switch button
    cardData.body.elements.push({ tag: "markdown", content: `*${t(this.#lang, "respondingAs", agentName)}*`, element_id: "footer_el" });
    if (isPrivateChat) {
      cardData.body.elements.push({
        tag: "action",
        actions: [
          {
            tag: "button",
            text: { tag: "plain_text", content: t(this.#lang, "switchAssistant") },
            type: "default",
            value: { action: "switch" },
          },
        ],
      });
    }

    const createResp = await fetch(`${FEISHU_BASE}/open-apis/cardkit/v1/cards`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify({
        type: "card_json",
        data: JSON.stringify(cardData),
      }),
    });

    if (!createResp.ok) {
      const err = await createResp.text();
      throw new Error(`CardKit create failed: ${createResp.status} ${err}`);
    }

    const createData = await createResp.json();
    if (createData.code !== 0) {
      throw new Error(`CardKit create error: ${createData.msg} (code=${createData.code})`);
    }

    const cardId = createData.data.card_id;

    // Step 2: Send card to chat
    const sendResp = await fetch(
      `${FEISHU_BASE}/open-apis/im/v1/messages?receive_id_type=chat_id`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`,
        },
        body: JSON.stringify({
          receive_id: chatId,
          msg_type: "interactive",
          content: JSON.stringify({ type: "card", data: { card_id: cardId } }),
        }),
      }
    );

    if (!sendResp.ok) {
      const err = await sendResp.text();
      throw new Error(`Message send failed: ${sendResp.status} ${err}`);
    }

    return {
      cardId,
      chatId,
      agentName,
      sequence: 1,
      isPrivateChat,
      accessToken,
    };
  }

  /**
   * Write "Thinking..." placeholder to the streaming card.
   *
   * @param {StreamContext} ctx
   */
  async writeThinking(ctx) {
    await this._putElementContent(ctx, t(this.#lang, "thinking"));
  }

  /**
   * Append accumulated content to the streaming card.
   * Uses prefix-append model: fullText contains ALL text so far.
   *
   * @param {StreamContext} ctx
   * @param {string} fullText - Complete accumulated text
   * @param {number} sequence - Monotonic sequence number
   */
  async appendContent(ctx, fullText, sequence) {
    ctx.sequence = sequence;
    await this._putElementContent(ctx, fullText);
  }

  /**
   * Close the streaming card (disable streaming_mode).
   *
   * @param {StreamContext} ctx
   */
  async finalizeReply(ctx) {
    ctx.sequence++;
    const resp = await fetch(
      `${FEISHU_BASE}/open-apis/cardkit/v1/cards/${ctx.cardId}/settings`,
      {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${ctx.accessToken}`,
        },
        body: JSON.stringify({
          settings: JSON.stringify({ config: { streaming_mode: false } }),
          sequence: ctx.sequence,
        }),
      }
    );

    if (!resp.ok) {
      console.warn(`CardKit finalize warning: ${resp.status}`);
    }
  }

  /**
   * Finalize the card with an error message, then close streaming.
   *
   * @param {StreamContext} ctx
   * @param {string} msg - Error message to display
   */
  async finalizeWithError(ctx, msg) {
    await this._putElementContent(ctx, `⚠️ ${msg}`);
    await this.finalizeReply(ctx);
  }

  /**
   * Send a plain text message (used as fallback when CardKit fails).
   *
   * @param {string} chatId
   * @param {string} text
   * @param {string} accessToken
   */
  async sendFallbackMessage(chatId, text, accessToken) {
    await fetch(
      `${FEISHU_BASE}/open-apis/im/v1/messages?receive_id_type=chat_id`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`,
        },
        body: JSON.stringify({
          receive_id: chatId,
          msg_type: "text",
          content: JSON.stringify({ text }),
        }),
      }
    );
  }

  /**
   * Update a selection card to show confirmation after user picks an agent.
   */
  async updateCardToConfirmation(messageId, agentName, accessToken) {
    const updatedCard = {
      config: { wide_screen_mode: true },
      header: {
        title: { tag: "plain_text", content: `✓ ${agentName}` },
        template: "green",
      },
      elements: [
        { tag: "markdown", content: this.#lang === "zh" ? `已选择 **${agentName}**，直接发消息开始对话。` : `Selected **${agentName}**. Send a message to start chatting.` },
      ],
    };
    try {
      await fetch(`${FEISHU_BASE}/open-apis/im/v1/messages/${messageId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${accessToken}` },
        body: JSON.stringify({ content: JSON.stringify(updatedCard) }),
      });
    } catch { /* best-effort */ }
  }

  /**
   * Add a reaction emoji to a message (e.g., THINKING indicator).
   * Returns the reaction_id for later removal.
   */
  async addReaction(messageId, emojiType, accessToken) {
    try {
      const resp = await fetch(
        `${FEISHU_BASE}/open-apis/im/v1/messages/${messageId}/reactions`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${accessToken}` },
          body: JSON.stringify({ reaction_type: { emoji_type: emojiType } }),
        }
      );
      const data = await resp.json();
      return data?.data?.reaction_id || null;
    } catch {
      return null;
    }
  }

  /**
   * Remove a previously added reaction.
   */
  async removeReaction(messageId, reactionId, accessToken) {
    if (!reactionId) return;
    try {
      await fetch(
        `${FEISHU_BASE}/open-apis/im/v1/messages/${messageId}/reactions/${reactionId}`,
        {
          method: "DELETE",
          headers: { Authorization: `Bearer ${accessToken}` },
        }
      );
    } catch { /* best-effort */ }
  }

  /**
   * Send an agent selection card with buttons for each available agent.
   *
   * @param {string} chatId
   * @param {Array<{agentId: string, agentName: string}>} agents
   * @param {string} accessToken
   */
  async sendSelectionCard(chatId, agents, accessToken, currentAgentId = null) {
    // Build buttons — highlight current selection
    const actions = agents.map((agent) => ({
      tag: "button",
      text: { tag: "plain_text", content: agent.agentId === currentAgentId ? `✓ ${agent.agentName}` : agent.agentName },
      type: agent.agentId === currentAgentId ? "default" : "primary",
      value: { agentId: agent.agentId },
    }));

    // Split into rows of 3
    const actionElements = [];
    for (let i = 0; i < actions.length; i += 3) {
      actionElements.push({ tag: "action", actions: actions.slice(i, i + 3) });
    }

    // Build description list
    const descLines = agents
      .map((a) => `• **${a.agentName}**${a.description ? ` — ${a.description}` : ""}`)
      .join("\n");

    const currentHint = currentAgentId
      ? `当前: **${agents.find(a => a.agentId === currentAgentId)?.agentName || currentAgentId}**\n\n`
      : "";

    // Legacy card format (no schema field, top-level elements)
    const cardJson = {
      config: { wide_screen_mode: true },
      header: {
        title: { tag: "plain_text", content: t(this.#lang, "selectPrompt") },
        template: "blue",
      },
      elements: [
        { tag: "markdown", content: `${currentHint}${descLines}` },
        { tag: "hr" },
        ...actionElements,
      ],
    };

    // Send directly as msg_type "interactive" with inline card content
    const resp = await fetch(
      `${FEISHU_BASE}/open-apis/im/v1/messages?receive_id_type=chat_id`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`,
        },
        body: JSON.stringify({
          receive_id: chatId,
          msg_type: "interactive",
          content: JSON.stringify(cardJson),
        }),
      }
    );

    const respData = await resp.json().catch(() => ({}));
    if (!resp.ok || respData.code !== 0) {
      console.warn("Selection card send failed:", JSON.stringify(respData).slice(0, 300));
      const names = agents.map((a) => a.agentName).join(", ");
      await this.sendFallbackMessage(chatId, `${t(this.#lang, "selectPrompt")} ${names}`, accessToken);
    }
  }

  /**
   * Internal: PUT element content to update the streaming card.
   */
  async _putElementContent(ctx, content) {
    const resp = await fetch(
      `${FEISHU_BASE}/open-apis/cardkit/v1/cards/${ctx.cardId}/elements/stream_el/content`,
      {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${ctx.accessToken}`,
        },
        body: JSON.stringify({
          content,
          sequence: ctx.sequence,
        }),
      }
    );

    if (!resp.ok) {
      console.warn(`CardKit PUT element warning: ${resp.status}`);
    }
    ctx.sequence++;
  }
}
