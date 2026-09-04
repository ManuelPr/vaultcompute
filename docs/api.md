# Public Python API

Blindfold `0.1.x` treats the names exported directly by `blindfold` as its
supported Python API. Imports from `blindfold.core`, `blindfold.tools`,
`blindfold.hosts` and `blindfold.ports` are advanced integration points and may
change between minor releases.

## Recommended Mode B surface

Create one `BlindfoldSession` for one conversation or user isolation boundary:

```python
from blindfold import BlindfoldSession, ProtectionError, TableQueryCapability
```

The normal data path consists of:

- `session.model_instructions`
- `session.call_protected_tool()` or `session.call_protected_tool_async()`
- `session.execute_authorized_query()` for a capability-approved table query
- `session.render_final_answer()` immediately before display

`session.protect_tool_result()` is supported for frameworks that invoke tools
before an interception hook runs. Prefer the `call_protected_tool*` methods
when possible: they make it harder to confuse the raw result with the protected
copy.

The session's `config`, `session_id`, `store`, and `policy` attributes are
available for inspection and advanced wiring. Applications should not replace
them while a session is active.

## Other package exports

- `TableQueryCapability` carries trusted, exact authority for table operations.
- `ProtectionError` means protection could not be completed; stop instead of
  forwarding the original result.
- `PLACEHOLDER_PROMPT`, `describe_config()`, and `describe_schema()` support
  custom prompt and tool-description wiring.
- `rehydrate()` remains supported for custom boundaries. Mode B applications
  should normally call `session.render_final_answer()` instead.

## Boundary the API cannot enforce

`render_final_answer()` returns user-visible cleartext. Do not append that
return value to model history or send it to another model call. A Python library
cannot intercept an SDK call made elsewhere in the application; keeping final
rendering at the UI boundary is part of the Mode B integration contract.

Within the `0.1.x` series, incompatible changes to these exports or method
signatures require a deprecation path. The exact shapes of private modules are
not covered by that promise.
