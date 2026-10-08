import { defineConfig, type Plugin } from "vite";

// POST /__shutdown stops the dev server (used by the Quit button).
function shutdownEndpoint(): Plugin {
  return {
    name: "landsimplex-shutdown",
    configureServer(server) {
      server.middlewares.use("/__shutdown", (req, res) => {
        // A plain cross-site POST skips CORS preflight; only accept same-origin.
        const origin = req.headers.origin;
        if (req.method !== "POST" || (origin && origin !== `http://${req.headers.host}`)) {
          res.statusCode = 403;
          res.end();
          return;
        }
        res.statusCode = 202;
        res.end();
        setTimeout(() => server.close().finally(() => process.exit(0)), 300);
      });
    },
  };
}

export default defineConfig({
  plugins: [shutdownEndpoint()],
  server: {
    port: 5173,
  },
});
