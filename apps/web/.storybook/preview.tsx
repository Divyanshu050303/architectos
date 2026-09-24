import "../styles/globals.css";

import type { Decorator, Preview } from "@storybook/nextjs-vite";
import { Inter, JetBrains_Mono } from "next/font/google";
import { useLayoutEffect } from "react";

import { TooltipProvider } from "../components/ui/tooltip";

// Same fonts as app/layout.tsx; applied to <html> so portalled dialogs and tooltips inherit them.
const inter = Inter({ subsets: ["latin"], display: "swap", variable: "--font-inter" });
const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-jetbrains-mono",
});

/** Stories are the visual regression reference (spec §75), so both themes must be checkable. */
function ThemedFrame({ dark, children }: { dark: boolean; children: React.ReactNode }) {
  useLayoutEffect(() => {
    document.documentElement.classList.add(inter.variable, jetbrainsMono.variable);
    document.documentElement.classList.toggle("dark", dark);
  }, [dark]);
  return (
    <TooltipProvider delayDuration={200}>
      <div className="bg-background p-6 text-fg">{children}</div>
    </TooltipProvider>
  );
}

const withTheme: Decorator = (Story, context) => (
  <ThemedFrame dark={context.globals.theme === "dark"}>
    <Story />
  </ThemedFrame>
);

const preview: Preview = {
  globalTypes: {
    theme: {
      description: "Colour theme",
      toolbar: { title: "Theme", icon: "mirror", items: ["light", "dark"], dynamicTitle: true },
    },
  },
  initialGlobals: { theme: "light" },
  decorators: [withTheme],
  parameters: {
    layout: "fullscreen",
    nextjs: { appDirectory: true },
  },
};

export default preview;
