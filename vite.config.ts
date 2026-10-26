import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({plugins:[react()],server:{port:5173,proxy:{'/api':process.env.ALFA_API_PROXY||'http://127.0.0.1:8000'}},build:{outDir:'dist'},test:{environment:'node',include:['frontend/src/**/*.test.ts*']}});
