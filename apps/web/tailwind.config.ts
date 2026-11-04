import type { Config } from 'tailwindcss';
const config: Config = { content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'], theme: { extend: { colors: { ink: '#0b1117', panel: '#121b24', line: '#243442', cyan: '#4fd1c5', violet: '#9b83ff', amber: '#f3b34c', mint: '#63e6b4' } } }, plugins: [] };
export default config;
