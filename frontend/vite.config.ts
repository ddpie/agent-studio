import { defineConfig, loadEnv } from 'vite'
import dns from 'node:dns'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Force IPv4 to avoid VPN/IPv6 connectivity issues with CloudFront
dns.setDefaultResultOrder('ipv4first')

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '..', 'AGENT_STUDIO_')
  const cfDomain = env.AGENT_STUDIO_API_URL || ''

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
      // Empty API_URL so browser uses relative paths → Vite proxy → CloudFront
      __AGENT_STUDIO_API_URL__: JSON.stringify(''),
    },
    server: {
      proxy: cfDomain ? {
        '/api': {
          target: cfDomain,
          changeOrigin: true,
          secure: true,
        },
      } : undefined,
    },
  }
})
