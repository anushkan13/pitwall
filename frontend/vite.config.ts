import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // a stamp per build, shown in the account menu, so it's easy to tell which build a browser is running
  define: { __BUILD_ID__: JSON.stringify(new Date().toISOString().replace(/[-:]/g, '').slice(0, 15)) },
})
