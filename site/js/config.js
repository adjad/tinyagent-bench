/* Site configuration. The benchmark's public name lives here and nowhere else:
 * every page title, the wordmark, the footer and the methodology read it from EDAT.config.
 * To rename the benchmark, change `name` and `longName` below. */
(function () {
  "use strict";
  const EDAT = (window.EDAT = window.EDAT || {});
  EDAT.config = {
    name: "EDAT",
    longName: "Edge Device Agentic Testing",
    tagline: "A benchmark for small language models used as agents on your own device.",
    /* Where the results store's export lives, relative to index.html. */
    dataRoot: "data/",
    /* Shown on the methodology page, where the harness comes from. */
    harnessOrigin: "Harbor",
    repository: "https://github.com/adjad/tinyagent-bench",
    /* First device a visitor sees when the URL names none. */
    defaultDeviceClass: "mac-ref",
    /* Display facts the export does not carry yet (see the hand-over: `device.name`, `device.fit_label`).
     * Keyed by device class. The export's own values win when present. */
    devices: {
      "mac-ref": { name: "MacBook Pro, M5 Pro, 24 GB", short: "Mac", kind: "laptop", fitLabel: "a 16 GB Mac", runtimeNote: "oMLX" },
      "iphone-ref": { name: "iPhone Air, 12 GB", short: "iPhone", kind: "phone", fitLabel: "the iPhone Air", runtimeNote: "MLX Swift" }
    },
    runtimes: { omlx: "oMLX", "mlx-swift": "MLX Swift", openrouter: "OpenRouter" },
    quants: { q4: "4-bit", q5: "5-bit", q6: "6-bit", q8: "8-bit", bf16: "16-bit" },
    /* The seven context sizes the performance tier measures (spec 04). */
    contextLevels: [512, 1024, 2048, 5120, 10240, 32768, 65536],
    contextLabels: ["512", "1k", "2k", "5k", "10k", "32k", "64k"]
  };
})();
