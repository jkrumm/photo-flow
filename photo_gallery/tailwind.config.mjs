/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/**/*.{astro,html,js,jsx,md,mdx,svelte,ts,tsx,vue}'],
  theme: {
    extend: {
      maxWidth: {
        'gallery': '1680px',
      },
      padding: {
        'gallery-desktop': '60px',
        'gallery-mobile': '30px',
      },
    },
  },
  plugins: [],
}
