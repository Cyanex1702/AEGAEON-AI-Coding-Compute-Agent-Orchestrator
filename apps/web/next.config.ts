import type { NextConfig } from "next";

const controllerUrl = process.env.AEGAEON_CONTROLLER_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["127.0.0.1"],
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        source: "/api/controller/:path*",
        destination: `${controllerUrl}/:path*`,
      },
    ];
  },
  turbopack: {
    root: process.cwd(),
  },
};

export default nextConfig;

