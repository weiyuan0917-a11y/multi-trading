const path = require("path");

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  allowedDevOrigins: ['192.168.1.9', 'localhost', '127.0.0.1', '::1'],
  turbopack: {
    root: path.resolve(__dirname),
  },
};

module.exports = nextConfig;
