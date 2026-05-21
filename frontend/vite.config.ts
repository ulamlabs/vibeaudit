import { defineConfig, loadEnv } from 'vite'
import { devtools } from '@tanstack/devtools-vite'

import { tanstackRouter } from '@tanstack/router-plugin/vite'

import viteReact from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const config = defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const extraHost = env.VITE_EXTRA_HOST?.trim()

  return {
    base: '/',
    build: {
      assetsDir: 'static',
    },
    server: {
      allowedHosts: ['localhost', ...(extraHost ? [extraHost] : [])],
      proxy: {
        '/api': 'http://localhost:8000',
      },
    },
    resolve: { tsconfigPaths: true },
    plugins: [
      devtools(),
      tailwindcss(),
      tanstackRouter({ target: 'react', autoCodeSplitting: true }),
      viteReact(),
    ],
  }
})

export default config
