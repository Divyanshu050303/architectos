# Component catalog

Reviewed, versioned specifications of infrastructure technologies. See
[docs/architecture/component-catalog.md](../../docs/architecture/component-catalog.md).

- `<category directory>/<entry>.yaml` is the current version of `<category directory>/<entry>`;
  `<category directory>/history/<entry>@<n>.yaml` are older versions, kept readable.
- Every claim has provenance; a `documented` claim cites a source with the date it was checked.
  What no source states is left out or marked `unknown` — never guessed.
- A published version is never edited: copy it to `history/`, increment `version`, change the new
  file, then run `make catalog-lock`. The catalog refuses to load an edited or unrecorded version.
