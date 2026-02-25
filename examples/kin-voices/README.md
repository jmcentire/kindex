# .kin Voice Files

Companies publish `.kin` files to encode their communication style, engineering standards, and values. Teams inherit these, and their repos inherit from teams. The knowledge graph carries the voice forward automatically.

## How It Works

```
~/.kindex/voices/anthropic.kin    # Org voice (public, downloadable)
    ^
    |  inherits
    |
~/Code/platform/.kin              # Platform team context
    ^
    |  inherits
    |
~/Code/payments-service/.kin      # Service-specific context
```

Each layer adds specificity. The payments service gets Anthropic's voice principles, the platform team's engineering standards, AND its own domain-specific context. Local values override ancestors.

## Using a Voice

```yaml
# my-project/.kin
name: my-project
audience: team
domains: [engineering]
inherits:
  - ~/.kindex/voices/anthropic.kin
```

Or place voices in `~/.kindex/voices/` and Kindex discovers them automatically via parent directory walk.

## Publishing a Voice

Any organization can publish a `.kin` file. It's just YAML:

```yaml
name: your-company-voice
audience: public
voice:
  tone: ...
  principles: [...]
  anti-patterns: [...]
standards:
  code_review: [...]
  communication: [...]
```

Publish it to your GitHub, share the URL, and teams can inherit from it. This is open infrastructure — no registry, no approval process, just files.

## Included Examples

- `anthropic.kin` — Precise, honest, safety-conscious communication
- `startup.kin` — Fast, pragmatic, ship-first engineering culture
