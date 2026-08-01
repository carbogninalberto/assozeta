import eslintPluginSvelte from 'eslint-plugin-svelte';
export default [
  // add more generic rule sets here, such as:
  // js.configs.recommended,
  ...eslintPluginSvelte.configs['flat/prettier'],
  {
    rules: {
        semi: [2, 'always']
    },
    languageOptions: {
        ecmaVersion: 2021,
        sourceType: 'module',
        globals: {
            __bakney: 'readonly',
            jQuery: 'readonly',
            moment: 'readonly',
            UiApp: 'readonly',
        }
    },
  }
];


// export default [
//     {
//         files: ['*.js'],
//         languageOptions: {
//             ecmaVersion: 2021,
//             sourceType: 'module',
//             globals: {
//                 __bakney: 'readonly',
//                 jQuery: 'readonly',
//                 moment: 'readonly',
//                 UiApp: 'readonly',
//             }
//         },
//         rules: {
//             semi: [2, 'always']
//         },
//         plugins: {
//         },
//         processor: null
//     },
//     {
//         files: ['*.svelte'],
//         languageOptions: {
//             ecmaVersion: 2021,
//             sourceType: 'module',
//             globals: {
//                 __bakney: 'readonly',
//                 jQuery: 'readonly',
//                 moment: 'readonly',
//                 UiApp: 'readonly',
//             }
//         },
//         rules: {
//             semi: [2, 'always']
//         },
//         processor: null,
//         plugins: {
//         }
//     }
// ];
