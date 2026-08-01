# assozeta

A Svelte-based dashboard for sports association management. Built with [Svelte](https://svelte.dev) and [Vite](https://vitejs.dev).

## License

This project is licensed under the GNU Affero General Public License v3.0 (AGPL-3.0). See [LICENSE](LICENSE) for details.

## Get started

Install the dependencies:

```bash
npm install
```

Start the development server:

```bash
npm run dev
```

Navigate to [localhost:5000](http://localhost:5000).

Self-hosted development mode:

```bash
npm run dev:selfhosted
npm run dev:assozeta
```

## Building for production

```bash
npm run build:vite:production
npm run start:vite
```

## Native builds

Android and iOS platform folders are intentionally not committed. If you need a native build, add the Capacitor platform folders locally and keep them out of source control.

## Linting

```bash
npx eslint src/routes/
npx eslint src/routes/ --fix
```

## Contributing

Contributions are welcome. Please open an issue or submit a pull request.
