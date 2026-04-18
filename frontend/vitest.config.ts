import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  define: {
    __AGENT_STUDIO_REGION__: JSON.stringify("us-east-1"),
    __AGENT_STUDIO_ACCOUNT_ID__: JSON.stringify(""),
    __AGENT_STUDIO_META_AGENT_ID__: JSON.stringify(""),
    __AGENT_STUDIO_COGNITO_USER_POOL_ID__: JSON.stringify(""),
    __AGENT_STUDIO_COGNITO_CLIENT_ID__: JSON.stringify(""),
    __AGENT_STUDIO_S3_BUCKET__: JSON.stringify(""),
    __AGENT_STUDIO_API_URL__: JSON.stringify(""),
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["node_modules", "dist", "e2e"],
  },
});
