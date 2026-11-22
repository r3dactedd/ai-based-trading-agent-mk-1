const api = process.env.API_INTERNAL_URL || "http://127.0.0.1:8000";

export default {
  output: "standalone",
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/live", destination: `${api}/live` }];
  },
};
