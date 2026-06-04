import type { Meta, StoryObj } from "@storybook/react-vite";
import { useEffect, useState } from "react";

/**
 * OfflineBanner uses the `useOnlineStatus` hook internally, so we cannot
 * drive it purely via props. Instead we render wrapper components that
 * simulate the offline/online DOM by directly rendering the same markup
 * the real component would produce.
 */
const meta: Meta = {
  title: "Common/OfflineBanner",
  parameters: {
    layout: "fullscreen",
  },
};

export default meta;
type Story = StoryObj;

function OfflineMock() {
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="offline-banner"
      className="sticky top-0 z-50 w-full bg-red-600 text-white text-center text-sm py-1.5"
    >
      You are offline. Changes won't be saved.
    </div>
  );
}

function BackOnlineMock() {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    const timer = setTimeout(() => setVisible(false), 3000);
    return () => clearTimeout(timer);
  }, []);

  if (!visible) return null;
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="online-banner"
      className="sticky top-0 z-50 w-full bg-emerald-600 text-white text-center text-sm py-1.5"
    >
      Back online!
    </div>
  );
}

export const Offline: Story = {
  render: () => (
    <div>
      <OfflineMock />
      <div className="p-6 text-gray-700 dark:text-gray-300">
        <p>Page content beneath the banner.</p>
      </div>
    </div>
  ),
};

export const BackOnline: Story = {
  render: () => (
    <div>
      <BackOnlineMock />
      <div className="p-6 text-gray-700 dark:text-gray-300">
        <p>The banner will auto-dismiss after 3 seconds.</p>
      </div>
    </div>
  ),
};

export const Hidden: Story = {
  render: () => (
    <div className="p-6 text-gray-700 dark:text-gray-300">
      <p>When online, the banner renders nothing. This is the normal state.</p>
    </div>
  ),
};
