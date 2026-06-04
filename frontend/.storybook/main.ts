import type { StorybookConfig } from "@storybook/react-vite";

const config: StorybookConfig = {
  stories: ["../src/**/*.stories.@(ts|tsx)"],
  addons: ["@storybook/addon-a11y"],
  framework: "@storybook/react-vite",
  viteFinal(config) {
    config.define = {
      ...config.define,
      global: "globalThis",
      __AGENT_STUDIO_REGION__: JSON.stringify("us-east-1"),
      __AGENT_STUDIO_ACCOUNT_ID__: JSON.stringify("123456789012"),
      __AGENT_STUDIO_META_AGENT_ID__: JSON.stringify("test-meta-agent"),
      __AGENT_STUDIO_COGNITO_USER_POOL_ID__: JSON.stringify("us-east-1_test"),
      __AGENT_STUDIO_COGNITO_CLIENT_ID__: JSON.stringify("testclient"),
      __AGENT_STUDIO_S3_BUCKET__: JSON.stringify("test-bucket"),
      __AGENT_STUDIO_API_URL__: JSON.stringify(""),
    };
    return config;
  },
};

export default config;
