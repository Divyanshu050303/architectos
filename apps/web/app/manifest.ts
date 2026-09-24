import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "ArchitectOS",
    short_name: "ArchitectOS",
    description: "Design today. Scale tomorrow.",
    start_url: "/app",
    display: "standalone",
    background_color: "#f7f8f6",
    theme_color: "#f7f8f6",
    icons: [
      { src: "/brand/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/brand/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/brand/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
