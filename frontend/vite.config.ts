import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '..', 'AGENT_STUDIO_')

  return {
    plugins: [react(), tailwindcss()],
    envDir: '..',
    define: {
      global: 'globalThis',
      __AGENT_STUDIO_REGION__: JSON.stringify(env.AGENT_STUDIO_REGION || 'us-east-1'),
      __AGENT_STUDIO_ACCOUNT_ID__: JSON.stringify(env.AGENT_STUDIO_ACCOUNT_ID || ''),
      __AGENT_STUDIO_META_AGENT_ID__: JSON.stringify(env.AGENT_STUDIO_META_AGENT_ID || ''),
      __AGENT_STUDIO_COGNITO_USER_POOL_ID__: JSON.stringify(env.AGENT_STUDIO_COGNITO_USER_POOL_ID || ''),
      __AGENT_STUDIO_COGNITO_CLIENT_ID__: JSON.stringify(env.AGENT_STUDIO_COGNITO_CLIENT_ID || ''),
      __AGENT_STUDIO_COGNITO_IDENTITY_POOL_ID__: JSON.stringify(env.AGENT_STUDIO_COGNITO_IDENTITY_POOL_ID || ''),
      __AGENT_STUDIO_S3_BUCKET__: JSON.stringify(env.AGENT_STUDIO_S3_BUCKET || ''),
    },
  }
})
