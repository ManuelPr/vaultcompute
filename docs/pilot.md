# Alpha pilot

The first pilot should involve 2–3 external users across two paths: a Python
developer integrating Mode B and a user of a supported host plugin. Findings
are evidence of specific runs, not certification of the whole product.

No participants have been recruited by this document. The maintainer chooses
who to invite and contacts them; the template below is only a draft.

## Shared setup

Use the exact alpha wheel or tagged source and synthetic data only. Record the
package version, operating system, Python version and installation method.
Plugin runs also need the exact host version and plugin revision. Do not use
the developer's existing environment as the only installation check.

Record elapsed setup time, unclear instructions, failed commands and any manual
fixes. Have the participant follow the documentation without coaching for the
first attempt, then record what help was needed.

## Path 1: Python library

1. Install the exact candidate into a new environment.
2. Run [examples/quickstart.py](../examples/quickstart.py). Verify that the two
   model-facing lines contain placeholders and the final line selects James.
3. Replace the synthetic tool with another synthetic JSON response; declare
   its table and at least one optional field. Use the same exact tool name in
   the schema and call.
4. Add the library to a minimal model loop the participant already controls.
   Record only the protected messages sent to the model. Keep final cleartext
   out of the next turn. Do not issue authority blindly for a model's query.
5. Change a required response path, alter an authorized query and try to render
   a token from another session. Record the refusal or redaction behavior.

Success means an independently installed package completes the declared task,
private synthetic markers do not appear in model-facing messages, failures are
understandable, and the participant can explain where final rendering belongs.
Document model-loop wiring separately from the scripted demo.

## Path 2: host plugin

Choose a host for which the participant can inspect the persisted transcript.
Follow its [setup guide](host-adapters.md), with a shared SQLite vault and a
protected synthetic tool. Do not enable arbitrary Python for this pilot.

1. Verify that the hooks and operation server start with the expected config.
2. Make one actual protected tool call and inspect the persisted model-facing
   result for placeholders and absence of the exact synthetic markers.
3. Query a declared table and inspect the next model-facing result.
4. Check the expected user experience: Claude Code display-only reveal on the
   supported adapter, or visible placeholders with the Codex adapter.
5. Exercise a missing required path and an unsupported result shape; verify
   the original does not continue to the model on the covered host path.

For Claude Code, use the opt-in version-pinned verification described in
[host-adapters.md](host-adapters.md). For Codex, record real-host evidence;
the unit fixtures alone are insufficient. Record uncovered tool paths as
limitations, and keep any host telemetry distinction explicit.

## Feedback record

Use the repository's **Alpha pilot feedback** issue form for nonsensitive
results. A report should contain:

- Participant role and path (no personal contact details required).
- Exact versions, candidate commit and installation command.
- Intended task and synthetic schema.
- Steps that worked, failed or required help; setup time.
- Expected versus observed model-facing and user-facing output.
- Whether the result came from the scripted demo or a real model/host run.
- The smallest next feature that would unblock the task, if any.

Suspected leaks go through [private security reporting](../SECURITY.md), not
the public feedback form. Do not attach real vaults, credentials or private
transcripts. Audit output is a diagnostic, not a proof of absence of leaks.

## Invitation draft

> I'm preparing the first VaultCompute alpha and looking for a short evaluation
> with synthetic data. It protects declared structured tool results before they
> reach a model, supports controlled queries over hidden tables and restores
> authorized values at the final display boundary. Would you try either the
> Python integration or a supported host plugin in a clean environment and
> record where setup or the workflow is unclear? The package is experimental;
> the pilot guide lists its limits and the private security reporting channel.

Before sending, add the candidate download/tag and the specific evaluation
path. This draft does not authorize automated messages to participants.
