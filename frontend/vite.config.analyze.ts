/**
 * Vite config for bundle analysis.
 * Used by `npm run build:analyze` — produces stats.html via rollup-plugin-visualizer.
 * Does NOT affect the normal `npm run build`.
 */
import { defineConfig, loadEnv } from 'vite'
import dns from 'node:dns'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { visualizer } from 'rollup-plugin-visualizer'

dns.setDefaultResultOrder('ipv4first')

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '..', 'AGENT_STUDIO_')

  const region = env.AGENT_STUDIO_REGION
  if (!region) {
    throw new Error(
      'AGENT_STUDIO_REGION is not set. Populate .env from .env.example ' +
      '(set it to us-east-1 or us-west-2) before running vite.'
    )
  }

  return {
    plugins: [
      react(),
      tailwindcss(),
      visualizer({
        filename: 'dist/stats.html',
        open: false,
        gzipSize: true,
        brotliSize: true,
        template: 'treemap',
      }),
    ],
    envDir: '..',
    define: {
      global: 'globalThis',
      __AGENT_STUDIO_REGION__: JSON.stringify(region),
      __AGENT_STUDIO_ACCOUNT_ID__: JSON.stringify(env.AGENT_STUDIO_ACCOUNT_ID || ''),
      __AGENT_STUDIO_META_AGENT_ID__: JSON.stringify(env.AGENT_STUDIO_META_AGENT_ID || ''),
      __AGENT_STUDIO_COGNITO_USER_POOL_ID__: JSON.stringify(env.AGENT_STUDIO_COGNITO_USER_POOL_ID || ''),
      __AGENT_STUDIO_COGNITO_CLIENT_ID__: JSON.stringify(env.AGENT_STUDIO_COGNITO_CLIENT_ID || ''),
      __AGENT_STUDIO_S3_BUCKET__: JSON.stringify(env.AGENT_STUDIO_S3_BUCKET || ''),
      __AGENT_STUDIO_API_URL__: JSON.stringify(''),
    },
  }
})
