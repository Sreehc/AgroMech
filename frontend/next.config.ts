import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["127.0.0.1"],
  experimental: {
    proxyClientMaxBodySize: "120mb",
  },
  async rewrites() {
    const apiBaseUrl = process.env.AGROMECH_API_BASE_URL ?? "http://127.0.0.1:8000";
    return [
      {
        source: "/backend/:path*",
        destination: `${apiBaseUrl}/:path*`,
      },
    ];
  },
};

export default nextConfig;
