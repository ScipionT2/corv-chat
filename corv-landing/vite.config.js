import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: '/static/landing-react/',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
});
