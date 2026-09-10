/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  turbopack: {
    root: new URL(".", import.meta.url).pathname,
  },
};

export default nextConfig;
