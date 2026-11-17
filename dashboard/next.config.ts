import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // A self-contained server in .next/standalone, which the Dockerfile copies.
  output: "standalone",
};

export default nextConfig;
