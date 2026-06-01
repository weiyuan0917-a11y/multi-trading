const path = require("path");

const customerBuild =
  process.env.MT_BUILD_TARGET === "customer" ||
  process.env.NEXT_PUBLIC_MT_BUILD_TARGET === "customer";
const distDir = process.env.NEXT_DIST_DIR || undefined;

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: customerBuild ? "standalone" : undefined,
  distDir,
  experimental: customerBuild
    ? {
        cpus: 1,
        staticGenerationMaxConcurrency: 1,
        staticGenerationMinPagesPerWorker: 1,
        staticGenerationRetryCount: 2,
      }
    : undefined,
  allowedDevOrigins: ['192.168.1.9', 'localhost', '127.0.0.1', '::1'],
  turbopack: {
    root: path.resolve(__dirname),
  },
};

module.exports = nextConfig;
