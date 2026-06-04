import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

// Tiered ruleset:
//   1. recommended (existing)               — basic correctness
//   2. recommendedTypeChecked (NEW)         — type-aware checks (needs parserOptions.project)
//      catches: floating promises, misused promises (forgotten await),
//               unsafe any, redundant nullish coalescing, etc.
//   3. stylisticTypeChecked (NEW)           — opinionated stylistic checks
//
// Per-rule overrides keep CI green during initial rollout — high-signal rules
// stay as 'error', noisier ones start as 'warn' and tighten over time.
export default defineConfig([
  globalIgnores([
    'dist', 'coverage', 'node_modules', '.vite', 'e2e',
    'playwright.config.ts',
    'vite.config.ts',
    'vite.config.analyze.ts',
    'vitest.config.ts',
    'knip.config.ts',
    '.storybook',
    'src/**/__tests__',
    'src/__tests__',
    'src/**/__stories__',
    'scripts',
    'src/mocks',
  ]),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommendedTypeChecked,
      tseslint.configs.stylisticTypeChecked,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2023,
      globals: globals.browser,
      parserOptions: {
        // projectService auto-discovers tsconfig per file — handles
        // vitest.config.ts and other files outside the app's tsconfig.
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      // ── high-signal: keep as error ────────────────────────────────────
      '@typescript-eslint/await-thenable': 'error',
      '@typescript-eslint/no-array-delete': 'error',
      '@typescript-eslint/no-for-in-array': 'error',
      '@typescript-eslint/no-implied-eval': 'error',

      // ── true positives but large baseline — start as warn, tighten later
      '@typescript-eslint/no-floating-promises': 'warn',
      '@typescript-eslint/no-misused-promises': ['warn', {
        checksVoidReturn: { attributes: false }, // JSX onClick=async is fine
      }],
      '@typescript-eslint/no-base-to-string': 'warn',
      '@typescript-eslint/no-unnecessary-type-assertion': 'warn',
      '@typescript-eslint/no-empty-function': 'warn',
      '@typescript-eslint/array-type': 'warn',
      '@typescript-eslint/prefer-regexp-exec': 'warn',
      '@typescript-eslint/no-redundant-type-constituents': 'warn',
      '@typescript-eslint/dot-notation': 'warn',
      '@typescript-eslint/consistent-generic-constructors': 'warn',

      // ── start as warn during rollout ──────────────────────────────────
      // Flip to 'error' once the existing baseline is fixed.
      '@typescript-eslint/no-unsafe-argument': 'warn',
      '@typescript-eslint/no-unsafe-assignment': 'warn',
      '@typescript-eslint/no-unsafe-call': 'warn',
      '@typescript-eslint/no-unsafe-member-access': 'warn',
      '@typescript-eslint/no-unsafe-return': 'warn',
      '@typescript-eslint/no-explicit-any': 'warn',
      '@typescript-eslint/restrict-template-expressions': 'warn',
      '@typescript-eslint/restrict-plus-operands': 'warn',
      '@typescript-eslint/require-await': 'warn',
      '@typescript-eslint/unbound-method': ['warn', { ignoreStatic: true }],

      // ── stylistic: warn ──────────────────────────────────────────────
      '@typescript-eslint/prefer-nullish-coalescing': 'warn',
      '@typescript-eslint/prefer-optional-chain': 'warn',
      '@typescript-eslint/consistent-type-definitions': 'off', // interface vs type — both fine
      '@typescript-eslint/consistent-indexed-object-style': 'off',

      // ── unused — keep strict; underscore-prefix to opt-out ───────────
      '@typescript-eslint/no-unused-vars': ['error', {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
        caughtErrorsIgnorePattern: '^_',
        destructuredArrayIgnorePattern: '^_',
      }],

      // react-refresh: HMR-correctness, not runtime correctness — warn-only
      // (these only matter in dev mode for fast-refresh).
      'react-refresh/only-export-components': 'warn',
      'no-useless-escape': 'warn',
      '@typescript-eslint/prefer-for-of': 'warn',
      // react-hooks 7.x added React-Compiler rules (set-state-in-effect,
      // preserve-manual-memoization, refs-in-render, impure-function-during-
      // render). They're correct but flag a lot of existing patterns (loading
      // state in effects, ref-based DOM measurements). Treat as warn until
      // patterns are migrated.
      'react-hooks/set-state-in-effect': 'warn',
      'react-hooks/preserve-manual-memoization': 'warn',
      'react-hooks/refs': 'warn',
      'react-hooks/purity': 'warn',
    },
  },
  // Tests are looser: any/unsafe-* in test mocks are routine.
  {
    files: ['**/*.{test,spec}.{ts,tsx}', 'src/test-setup.ts', '**/__tests__/**'],
    rules: {
      '@typescript-eslint/no-unsafe-argument': 'off',
      '@typescript-eslint/no-unsafe-assignment': 'off',
      '@typescript-eslint/no-unsafe-call': 'off',
      '@typescript-eslint/no-unsafe-member-access': 'off',
      '@typescript-eslint/no-unsafe-return': 'off',
      '@typescript-eslint/no-explicit-any': 'off',
      '@typescript-eslint/unbound-method': 'off',
      '@typescript-eslint/require-await': 'off',
      '@typescript-eslint/no-floating-promises': 'off',
    },
  },
])
