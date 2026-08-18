const backend = process.env.ECHO_API_SERVER || "http://localhost:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  async rewrites() {
    return [
      {
        source: "/api-proxy/:path*",
        destination: `${backend}/:path*`
      }
    ];
  }
};

export default nextConfig;
