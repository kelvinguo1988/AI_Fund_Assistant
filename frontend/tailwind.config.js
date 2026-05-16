/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'signal-buy': 'var(--signal-buy)',
        'signal-sell': 'var(--signal-sell)',
        'signal-hold': 'var(--signal-hold)',
        'signal-buy-light': 'var(--signal-buy-light)',
        'signal-sell-light': 'var(--signal-sell-light)',
      },
    },
  },
  plugins: [],
  // 禁止 Tailwind 的 preflight 覆盖 MUI 样式
  corePlugins: {
    preflight: false,
  },
};
