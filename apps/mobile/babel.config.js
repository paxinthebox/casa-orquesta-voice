// Babel config for Expo SDK 52 + React Native 0.76 (New Architecture enabled).
// `expo-router` registers its own preset, so we just extend `babel-preset-expo`
// and add the Reanimated plugin LAST (per react-native-reanimated docs).
module.exports = function (api) {
  api.cache(true);
  return {
    presets: ['babel-preset-expo'],
    plugins: [
      [
        'module-resolver',
        {
          // Only rewrite our workspace aliases — never bare npm specifiers like `expo`.
          alias: {
            '@': './src',
            '@assets': './assets',
          },
        },
      ],
      // Reanimated must be listed LAST.
      'react-native-reanimated/plugin',
    ],
  };
};
