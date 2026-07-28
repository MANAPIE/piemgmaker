import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async redirects() {
    // 리뷰·확정 페이지가 /jobs/:id → /history/:id 로 이동 (북마크·구 링크 호환)
    return [{ source: "/jobs/:id", destination: "/history/:id", permanent: false }];
  },
};

export default nextConfig;
