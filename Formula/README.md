# Homebrew Formula

The canonical Bernstein Homebrew formula lives at the
[`chernistry/homebrew-tap`](https://github.com/chernistry/homebrew-tap) repo.

Install with:

```bash
brew tap chernistry/tap
brew install bernstein
```

The previous `Formula/bernstein.rb` in this repo was unused dead code (not consumed
by `publish-homebrew.yml` and not synced with the live tap). It was removed in
favour of pointing every consumer at the tap directly. The CI-side template
that generates the tap formula on each release lives at
[`packaging/homebrew/bernstein.rb`](../packaging/homebrew/bernstein.rb).
