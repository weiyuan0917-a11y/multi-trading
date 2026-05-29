const path = require("path");

const customerBuild =
  process.env.MT_BUILD_TARGET === "customer" ||
  process.env.NEXT_PUBLIC_MT_BUILD_TARGET === "customer";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: customerBuild ? "standalone" : undefined,
  allowedDevOrigins: ['192.168.1.9', 'localhost', '127.0.0.1', '::1'],
  turbopack: {
    root: path.resolve(__dirname),
  },
};

module.exports = nextConfig;
