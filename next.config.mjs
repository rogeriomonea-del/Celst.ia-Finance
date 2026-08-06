/** @type {import('next').NextConfig} */

// Em desenvolvimento, aponte NEXT_PUBLIC_ENGINE_URL para o servidor Python local
// (ex.: http://127.0.0.1:8000, de `python3 -m engine.server`). Em produção a
// variável fica vazia e as chamadas caem na função serverless api/planilha.py.
const engineUrl = (process.env.NEXT_PUBLIC_ENGINE_URL || "").replace(/\/+$/, "");

const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  experimental: {
    serverActions: {
      bodySizeLimit: "4mb",
    },
  },
  async rewrites() {
    return [
      {
        source: "/api/py/:path*",
        destination: engineUrl ? `${engineUrl}/:path*` : "/api/:path*",
      },
    ];
  },
};

export default nextConfig;
