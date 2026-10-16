// Adds the light-dark() fallback for browsers before 2024 (Safari < 17.5, Chrome < 123, Firefox < 120),
// which Tailwind leaves out: Lightning CSS turns each light-dark(a, b) into custom-property toggles that
// follow color-scheme. Nothing else changes. Reads CSS on stdin, writes it to stdout (apps/core/assets.py).
const { transform, Features } = require("lightningcss");

const version = (major) => major << 16;
const chunks = [];
process.stdin.on("data", (chunk) => chunks.push(chunk));
process.stdin.on("end", () => {
  const { code } = transform({
    filename: "app.min.css",
    code: Buffer.concat(chunks),
    minify: true,
    targets: { chrome: version(100), firefox: version(100), safari: version(15), ios_saf: version(15), samsung: version(17) },
    include: Features.LightDark,
    exclude: ~Features.LightDark >>> 0, // every other feature as Tailwind emitted it
  });
  process.stdout.write(code);
});
