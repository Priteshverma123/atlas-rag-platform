/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "Segoe UI", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      colors: {
        brand: { 50: "#ecfdf8", 100: "#d1faee", 400: "#34d3b2", 500: "#14b896", 600: "#0b9479", 700: "#0d7663" },
      },
    },
  },
  plugins: [],
};
