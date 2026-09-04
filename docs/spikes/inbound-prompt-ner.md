# Spike: inbound prompt PII/NER

## Question

Can Blindfold hide personal data already present in a user's prompt by adding a
NER pass before the prompt reaches the model?

## Result

NER detection alone is not a complete feature and must not be presented as a
privacy boundary. Consider:

```text
user: What is Andrea Tuscano's salary?
model sees: What is ⟦tok_…⟧'s salary?
model calls: get_salary(name="⟦tok_…⟧")
```

The downstream tool needs the real name. Blindfold would therefore need a
trusted input boundary that resolves exact, same-session placeholders inside
tool arguments immediately before invocation. Adding only prompt replacement
would either break the tool call or tempt an application to reveal the name to
the model again.

The coherent flow is:

```text
user prompt
  -> optional entity detector
  -> prompt placeholders sent to model
  -> model proposes tool arguments containing placeholders
  -> trusted dispatcher resolves allowed argument placeholders
  -> real tool runs locally
  -> Blindfold protects its result
  -> protected result returns to model
```

## What a production version needs

1. A detector adapter, not a detector hidden in core. Deployments must be able
   to choose a local NER engine and language model.
2. Span validation: offsets must be in range, non-empty and non-overlapping.
3. Longest-span-first replacement so `Andrea Tuscano` is not split into two
   unrelated identities.
4. Same-session resolution at the trusted tool-input boundary, with an explicit
   allow-list of arguments that may contain inbound placeholders.
5. Separate lineage such as `prompt_entity`, so audit can distinguish a value
   supplied by the user from a value returned by a tool.
6. Tests for false negatives, overlapping entities, Unicode offsets, repeated
   names, forged tokens, cross-session tokens and tools that echo their input.
7. Documentation that a detector miss passes cleartext. Statistical NER cannot
   offer the deterministic “declared path means protected” guarantee of tool
   result tokenization.

## Decision for 0.1.0

Do not add NER or a new dependency to the core release. The missing trusted
tool-input boundary means a detector-only implementation would be incomplete,
and false negatives could make it look safer than it is.

Keep this as an optional post-0.1 feature. Its first implementation should be a
Mode B experiment containing both prompt protection and tool-argument
resolution; only then evaluate detector quality on representative languages and
data. Until that exists, prompts remain outside Blindfold's protection claim.
