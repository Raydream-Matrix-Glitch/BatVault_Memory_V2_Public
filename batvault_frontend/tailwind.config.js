/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,jsx,ts,tsx}"],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        darkbg: "#02141a",
        vaultred: "#ff0030",
        copy: "#d1d5db",
        // neon-cyberpunk palette for Memory UI
        neonCyan: "#00e0e0",
        surface: "#0a1a23",
      },
      // custom box shadows for neon glow effects
      boxShadow: {
        "neon-red": "0 0 8px rgba(255, 0, 48, 0.6)",
        "neon-cyan": "0 0 8px rgba(0, 224, 224, 0.6)",
      },
    },
  },
  plugins: [
    require("@tailwindcss/typography"),
    require("@tailwindcss/forms"),
    function ({ addComponents }) {
      addComponents({
        // Shell that owns the whole chat page
        ".chat-shell": {
          "@apply relative flex min-h-screen flex-col bg-darkbg text-copy": {},
        },

        // Scrollable messages zone
        ".chat-scroll": {
          "@apply flex-1 overflow-y-auto py-6": {},
        },

        // Single source of truth for horizontal bounds (messages AND input use it)
        ".chat-wrapper": {
          width: "100%",
          maxWidth: "var(--chat-max-w, 860px)", // <- fixed default lane width
          marginLeft: "auto",
          marginRight: "auto",
          paddingLeft: "1rem",
          paddingRight: "1rem",
        },

        // Each message line: a full-width flex row so we can justify left/right
        ".message-row": {
          "@apply w-full flex": {},
        },

        // The chat bubble itself: auto width (content) but capped by the lane
        ".chat-bubble": {
          "@apply relative inline-block rounded-2xl px-4 py-3": {},
          // cap to the lane minus wrapper padding (1rem each side)
          maxWidth: "min(100%, calc(var(--chat-max-w, 860px) - 2rem))",
          // allow long words/code to wrap instead of expanding the bubble
          wordBreak: "break-word",
        },

        ".bubble-user": {
          "@apply bg-blue-600 text-white self-end": {},
        },

        ".bubble-assistant": {
          "@apply bg-neutral-100 dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100 self-start": {},
        },
      });
    },
  ],
};
