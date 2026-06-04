import type { KnipConfig } from 'knip';

const config: KnipConfig = {
  entry: ['src/main.tsx'],
  project: ['src/**/*.{ts,tsx}'],
  ignore: [
    '**/__stories__/*',
    '**/__tests__/*',
    'e2e/**',
    'src/vite-env.d.ts',
  ],
  ignoreDependencies: [
    // Dynamically loaded at runtime (Web Worker / CDN)
    'pyodide',
    // CSS imported in markdown renderer
    'katex',
    // Vite plugins (referenced in vite.config.ts, not in src/)
    '@tailwindcss/vite',
    '@vitejs/plugin-react',
    // Type packages used implicitly
    '@types/react',
    '@types/react-dom',
    '@types/node',
    '@types/react-syntax-highlighter',
    // Test infrastructure
    '@testing-library/dom',
    '@testing-library/jest-dom',
    '@testing-library/react',
    '@vitest/coverage-v8',
    'jsdom',
    // Storybook (has its own entry points)
    '@storybook/addon-a11y',
    '@storybook/addon-vitest',
    '@storybook/react-vite',
    'storybook',
    // Playwright (separate test runner)
    '@playwright/test',
    // ESLint ecosystem
    '@eslint/js',
    'eslint-plugin-react-hooks',
    'eslint-plugin-react-refresh',
    'globals',
    'typescript-eslint',
    // Used by Vite config
    'dotenv',
  ],
};

export default config;
