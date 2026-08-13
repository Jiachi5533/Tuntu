# Contributing

Contributions are welcome. Keep the core content-neutral and put
source-specific behavior behind the protocols in `rankarr.contracts`.

Before opening a pull request:

1. Add tests for new behavior.
2. Run `python -m unittest discover -s tests -v`.
3. Document new plugin configuration and failure modes.
4. Do not commit credentials, cookies, downloaded media, or private data.

Provider contributions must use documented or publicly accessible interfaces
and must not bypass access controls.

