/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  webpack: (config) => {
    // Some wallet-adapter transitive deps still reference node built-ins.
    config.resolve.fallback = { ...config.resolve.fallback, fs: false, path: false, os: false };
    return config;
  },
};

export default nextConfig;
