/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./static/index.html", "./static/js/**/*.js"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "monospace"],
      },
    },
  },
};
