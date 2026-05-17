/**
 * Adapter Manager — supervisor for multiple channel adapters within a workspace.
 *
 * Responsibilities:
 *   - Load all channel configs for the workspace from DynamoDB
 *   - Instantiate one adapter per active channel
 *   - Supervisor restart: auto-reconnect crashed adapters after 5s
 *   - Poll DDB every 30s for configVersion changes (hot-reload)
 *   - Write lastHeartbeatAt to DDB every 60s
 *   - Update lastProcessedAt after each message forwarded
 */

import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import {
  DynamoDBDocumentClient,
  QueryCommand,
  UpdateCommand,
} from "@aws-sdk/lib-dynamodb";
import { FeishuAdapter } from "./feishu-adapter.mjs";

const TABLE_NAME = "agent-studio-channels";
const CONFIG_POLL_INTERVAL_MS = 30_000;
const HEARTBEAT_INTERVAL_MS = 60_000;
const RESTART_DELAY_MS = 5_000;

export class AdapterManager {
  /** @type {string} */
  #workspaceId;

  /** @type {string} */
  #region;

  /** @type {DynamoDBDocumentClient} */
  #ddb;

  /** @type {Map<string, { adapter: FeishuAdapter; configVersion: number; restartTimer?: NodeJS.Timeout }>} */
  #adapters = new Map();

  /** @type {((event: object) => Promise<void>)|null} */
  #eventCallback = null;

  /** @type {Map<string, { platformConfig: object; secret: object }>} */
  #secrets;

  /** @type {NodeJS.Timeout|null} */
  #configPollTimer = null;

  /** @type {NodeJS.Timeout|null} */
  #heartbeatTimer = null;

  /** @type {boolean} */
  #stopped = false;

  /**
   * @param {object} opts
   * @param {string} opts.workspaceId
   * @param {string} opts.region
   * @param {Map<string, { platformConfig: object; secret: object }>} opts.secrets — channelId → credentials
   */
  constructor({ workspaceId, region, secrets }) {
    this.#workspaceId = workspaceId;
    this.#region = region;
    this.#secrets = secrets;

    const client = new DynamoDBClient({ region });
    this.#ddb = DynamoDBDocumentClient.from(client);
  }

  /**
   * Register event callback for all adapters.
   * @param {(event: object) => Promise<void>} callback
   */
  onEvent(callback) {
    this.#eventCallback = callback;
  }

  /**
   * Start all adapters and background loops.
   */
  async start() {
    // Load initial configs and start adapters
    const channels = await this.#loadChannelConfigs();
    for (const channel of channels) {
      await this.#startAdapter(channel);
    }

    // Start config polling
    this.#configPollTimer = setInterval(
      () => this.#pollConfigChanges(),
      CONFIG_POLL_INTERVAL_MS,
    );

    // Start heartbeat
    this.#heartbeatTimer = setInterval(
      () => this.#writeHeartbeats(),
      HEARTBEAT_INTERVAL_MS,
    );

    // Write initial heartbeat
    await this.#writeHeartbeats();

    console.log(
      `[AdapterManager] Started: workspace=${this.#workspaceId}, adapters=${this.#adapters.size}`,
    );
  }

  /**
   * Stop all adapters and background loops.
   */
  async stop() {
    this.#stopped = true;

    if (this.#configPollTimer) {
      clearInterval(this.#configPollTimer);
      this.#configPollTimer = null;
    }
    if (this.#heartbeatTimer) {
      clearInterval(this.#heartbeatTimer);
      this.#heartbeatTimer = null;
    }

    // Disconnect all adapters
    const disconnectPromises = [];
    for (const [channelId, entry] of this.#adapters) {
      if (entry.restartTimer) clearTimeout(entry.restartTimer);
      disconnectPromises.push(
        entry.adapter.disconnect().catch((err) => {
          console.error(
            `[AdapterManager] Error disconnecting ${channelId}:`,
            err,
          );
        }),
      );
    }
    await Promise.all(disconnectPromises);
    this.#adapters.clear();

    // Final heartbeat flush
    await this.#writeHeartbeats();

    console.log(`[AdapterManager] Stopped: workspace=${this.#workspaceId}`);
  }

  /**
   * Load all channel configs for this workspace from DynamoDB.
   * @returns {Promise<object[]>}
   */
  async #loadChannelConfigs() {
    const result = await this.#ddb.send(
      new QueryCommand({
        TableName: TABLE_NAME,
        KeyConditionExpression: "workspaceId = :ws",
        ExpressionAttributeValues: { ":ws": this.#workspaceId },
      }),
    );
    return (result.Items || []).filter(
      (item) => item.status === "active" || item.status === "provisioning",
    );
  }

  /**
   * Start a single adapter for a channel config.
   * @param {object} channel — DDB item
   */
  async #startAdapter(channel) {
    const channelId = channel.sk;
    const channelType = channel.channelType;

    // Only Feishu supported in V1
    if (channelType !== "feishu") {
      console.warn(
        `[AdapterManager] Unsupported channel type: ${channelType}, skipping ${channelId}`,
      );
      return;
    }

    const creds = this.#secrets.get(channelId);
    if (!creds) {
      console.error(
        `[AdapterManager] No credentials for channel ${channelId}, skipping`,
      );
      return;
    }

    const adapter = new FeishuAdapter(channelId, this.#workspaceId);

    // Wire event callback with supervisor restart on disconnect/error
    adapter.onEvent(async (event) => {
      if (this.#eventCallback) {
        await this.#eventCallback(event);
      }
      // Update lastProcessedAt
      await this.#updateLastProcessed(channelId);
    });

    try {
      await adapter.connect(creds.platformConfig, creds.secret);
      this.#adapters.set(channelId, {
        adapter,
        configVersion: channel.configVersion || 0,
      });
    } catch (err) {
      console.error(
        `[AdapterManager] Failed to start adapter ${channelId}:`,
        err,
      );
      this.#scheduleRestart(channel);
    }
  }

  /**
   * Schedule automatic restart of a failed adapter after delay.
   * @param {object} channel
   */
  #scheduleRestart(channel) {
    if (this.#stopped) return;

    const channelId = channel.sk;
    console.log(
      `[AdapterManager] Scheduling restart for ${channelId} in ${RESTART_DELAY_MS}ms`,
    );

    const restartTimer = setTimeout(async () => {
      if (this.#stopped) return;
      console.log(`[AdapterManager] Restarting adapter ${channelId}`);

      // Remove old entry if exists
      const existing = this.#adapters.get(channelId);
      if (existing) {
        await existing.adapter.disconnect().catch(() => {});
        this.#adapters.delete(channelId);
      }

      await this.#startAdapter(channel);
    }, RESTART_DELAY_MS);

    // Track timer so we can clear it on stop
    const entry = this.#adapters.get(channelId);
    if (entry) {
      entry.restartTimer = restartTimer;
    } else {
      // Not yet in map; store temporarily
      this.#adapters.set(channelId, {
        adapter: new FeishuAdapter(channelId, this.#workspaceId),
        configVersion: channel.configVersion || 0,
        restartTimer,
      });
    }
  }

  /**
   * Poll DynamoDB for configVersion changes.
   * If a channel's version changed, disconnect the old adapter and reconnect.
   */
  async #pollConfigChanges() {
    if (this.#stopped) return;

    try {
      const channels = await this.#loadChannelConfigs();
      const currentIds = new Set();

      for (const channel of channels) {
        const channelId = channel.sk;
        currentIds.add(channelId);

        const existing = this.#adapters.get(channelId);
        const newVersion = channel.configVersion || 0;

        if (!existing) {
          // New channel added — start adapter
          console.log(
            `[AdapterManager] New channel detected: ${channelId}, starting adapter`,
          );
          await this.#startAdapter(channel);
        } else if (existing.configVersion < newVersion) {
          // Config changed — hot-reload
          console.log(
            `[AdapterManager] Config changed for ${channelId}: v${existing.configVersion} → v${newVersion}`,
          );
          await existing.adapter.disconnect().catch(() => {});
          this.#adapters.delete(channelId);
          await this.#startAdapter(channel);
        }
      }

      // Remove adapters for deleted/paused channels
      for (const [channelId, entry] of this.#adapters) {
        if (!currentIds.has(channelId)) {
          console.log(
            `[AdapterManager] Channel removed/paused: ${channelId}, disconnecting`,
          );
          if (entry.restartTimer) clearTimeout(entry.restartTimer);
          await entry.adapter.disconnect().catch(() => {});
          this.#adapters.delete(channelId);
        }
      }
    } catch (err) {
      console.error(`[AdapterManager] Config poll error:`, err);
    }
  }

  /**
   * Write lastHeartbeatAt for all active adapters.
   */
  async #writeHeartbeats() {
    const now = new Date().toISOString();
    const promises = [];

    for (const [channelId] of this.#adapters) {
      promises.push(
        this.#ddb
          .send(
            new UpdateCommand({
              TableName: TABLE_NAME,
              Key: { workspaceId: this.#workspaceId, sk: channelId },
              UpdateExpression: "SET lastHeartbeatAt = :ts",
              ExpressionAttributeValues: { ":ts": now },
            }),
          )
          .catch((err) => {
            console.error(
              `[AdapterManager] Heartbeat write failed for ${channelId}:`,
              err,
            );
          }),
      );
    }

    await Promise.all(promises);
  }

  /**
   * Update lastProcessedAt after a message is forwarded.
   * @param {string} channelId
   */
  async #updateLastProcessed(channelId) {
    try {
      await this.#ddb.send(
        new UpdateCommand({
          TableName: TABLE_NAME,
          Key: { workspaceId: this.#workspaceId, sk: channelId },
          UpdateExpression: "SET lastProcessedAt = :ts",
          ExpressionAttributeValues: { ":ts": Date.now() },
        }),
      );
    } catch (err) {
      console.error(
        `[AdapterManager] lastProcessedAt update failed for ${channelId}:`,
        err,
      );
    }
  }
}
