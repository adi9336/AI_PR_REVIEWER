import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The dashboard renders server-side; no client API exposure.
  // API_BASE_URL + GOVERNANCE_API_KEY come from frontend/.env.local.
  reactStrictMode: true,
};

export default nextConfig;
