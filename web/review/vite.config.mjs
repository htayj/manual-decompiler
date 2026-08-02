import { defineConfig } from "vite";
import { reviewProjectPlugin } from "./server/review-project.mjs";

/**
 * The review server intentionally binds only to IPv4 loopback.  `fs.strict`
 * also keeps Vite itself from exposing the manifest's parent directory; the
 * review middleware is the sole route to declared project assets.
 */
export default defineConfig({
  plugins: [reviewProjectPlugin()],
  server: {
    host: "127.0.0.1",
    strictPort: true,
    fs: {
      strict: true,
      allow: [new URL(".", import.meta.url).pathname],
    },
  },
  preview: {
    host: "127.0.0.1",
    strictPort: true,
  },
});
