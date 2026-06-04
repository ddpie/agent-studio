import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import ValidationBanner from "../ValidationBanner";
import type { ValidationResult } from "../../../lib/types/validation";

const meta: Meta<typeof ValidationBanner> = {
  title: "Shared/ValidationBanner",
  component: ValidationBanner,
  decorators: [
    (Story) => (
      <div className="max-w-2xl mx-auto p-4 bg-white dark:bg-gray-900">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof ValidationBanner>;

const validResult: ValidationResult = {
  valid: true,
  errors: [],
  warnings: [],
};

const warningsResult: ValidationResult = {
  valid: true,
  errors: [],
  warnings: [
    "Tool 'search_web' is defined but never referenced in the system prompt",
    "System prompt exceeds 2000 tokens — consider trimming for faster responses",
  ],
};

const errorsResult: ValidationResult = {
  valid: false,
  errors: [
    "Missing required field: model_id",
    "Tool function 'analyze_data' has invalid return type — must return str",
  ],
  warnings: [
    "Agent name 'my-agent' contains a hyphen — only alphanumeric characters are allowed",
  ],
};

export const Success: Story = {
  args: {
    result: validResult,
    onDismiss: fn(),
  },
};

export const WithWarnings: Story = {
  args: {
    result: warningsResult,
    onDismiss: fn(),
    onAutoFix: fn(),
    autoFixing: false,
  },
};

export const WithErrors: Story = {
  args: {
    result: errorsResult,
    onDismiss: fn(),
    onAutoFix: fn(),
    autoFixing: false,
  },
};

export const AutoFixing: Story = {
  args: {
    result: errorsResult,
    onDismiss: fn(),
    onAutoFix: fn(),
    autoFixing: true,
  },
};

export const NullResult: Story = {
  args: {
    result: null,
    onDismiss: fn(),
  },
  parameters: {
    docs: {
      description: { story: "When result is null, the banner renders nothing." },
    },
  },
};
