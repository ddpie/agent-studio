import { Toaster as SonnerToaster } from "sonner";
import useIsDark from "../../hooks/useIsDark";

export default function Toaster() {
  const isDark = useIsDark();
  return (
    <SonnerToaster
      position="bottom-right"
      richColors
      closeButton
      theme={isDark ? "dark" : "light"}
    />
  );
}
