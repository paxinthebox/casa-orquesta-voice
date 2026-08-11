// Metro bundler config. Default Expo behaviour is fine; we just enable
// resolution for the workspace alias `@/*` and ensure SVGs route through
// `react-native-svg-transformer` when (future) icons land.
const { getDefaultConfig } = require('expo/metro-config');
const path = require('path');

const config = getDefaultConfig(__dirname);

// Workspace alias — keeps `@/...` imports working at runtime to match
// the TS paths in tsconfig.json.
config.resolver.alias = {
  ...(config.resolver.alias || {}),
  '@': path.resolve(__dirname, 'src'),
  '@assets': path.resolve(__dirname, 'assets'),
};

// Source extensions: tsx/ts/jsx/js are default; add .mjs for ESM-only deps.
config.resolver.sourceExts = Array.from(
  new Set([...(config.resolver.sourceExts || []), 'mjs'])
);

module.exports = config;
