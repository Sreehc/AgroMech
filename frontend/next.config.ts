import type { NextConfig } from "next";

// 生产使用静态导出，业务请求 /backend/* 由宿主 Nginx 反代到 FastAPI。
// 本地 dev 没有 Nginx，这里用 rewrite 把 /backend/* 代理到本地后端；
// export 模式与 rewrites 不能共存，因此按环境切换。
const isDev = process.env.NODE_ENV === "development";
const backendOrigin = process.env.AGROMECH_BACKEND_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  ...(isDev ? {} : { output: "export" }),
  allowedDevOrigins: ["127.0.0.1"],
  experimental: {
    proxyClientMaxBodySize: "120mb",
  },
  ...(isDev
    ? {
        async rewrites() {
          return [
            {
              source: "/backend/:path*",
              destination: `${backendOrigin}/:path*`,
            },
          ];
        },
      }
    : {}),
};

export default nextConfig;
