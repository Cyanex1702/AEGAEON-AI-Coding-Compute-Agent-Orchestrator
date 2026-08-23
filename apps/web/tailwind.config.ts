import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{js,ts,jsx,tsx,mdx}", "./components/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        ink: "#07090d",
        panel: "#0c0f14",
        line: "#20252e",
        acid: "#c8ff38",
        cyan: "#55d9ff",
      },
      boxShadow: {
        glow: "0 0 24px rgba(200,255,56,.14)",
      },
    },
  },
  plugins: [],
};

export default config;

