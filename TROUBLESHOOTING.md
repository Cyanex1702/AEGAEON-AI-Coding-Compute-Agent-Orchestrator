# Troubleshooting

Start with **Getting started → Inspect setup**. Download its report if you need
to describe a setup problem. It contains app/Python versions, OS and tool status;
it excludes paths, credentials and source. Downloading it does not upload it.

## App does not open

Run **Start AEGAEON.bat** from the installation folder. Inspect `logs` there if
startup fails. The installed default is `%LOCALAPPDATA%\AEGAEON-Lite`. Stop a
running older build before starting a newly installed one. Do not start multiple
app processes against the same data folder. The manual source-ZIP launcher keeps
a terminal open; keep it open until you finish and stop it with Ctrl+C.

## Python or Git is missing

The single-file installer checks Python 3.11+ and Git. Missing tools are offered
through WinGet. Read its prompts. If WinGet is unavailable, install from the
official Python/Git sites and rerun setup. Restart the app after changing PATH.
No third-party Python packages are required by the app itself. Your coding
project and inference runtime can have their own dependencies.

## Model not connected

Start your inference server separately, then save its endpoint and exact alias
in **Model settings**. The server must list that alias. For llama.cpp, the
documented configuration uses `--alias local` and a loopback `/v1` endpoint.
Click **Test saved model connection** after saving. A model list is not an
inference test; a passed fixed-JSON probe is not proof of coding quality.

If JSON generation fails or truncates, try a smaller request and fewer source
files. Keep enough output budget for a complete action object. The GUI starts
with 4K/8K context guidance. No simulated demo fallback runs on your own project.

## A check cannot find pytest, Node or another dependency

Review each worker/local check policy and use **Check local setup**. It discovers
executables and working directories; it does not install project dependencies.
Install the dependencies in the environment where that check will run, or choose
the project's actual commands. A notebook's passing report does not establish
that the same commands pass on your PC. Local acceptance evidence is separate.

## Result ZIP is rejected

Select the original project and the exact task attempt that produced the result.
Do not change its baseline while the worker runs. Retries have different task
IDs. Stale, duplicate, out-of-scope or tampered results are rejected. Use portable
accepted-source backups when moving work to another folder; a result ZIP and a
portable source checkpoint serve different purposes.

## Notebook session ends or setup fails

Keep the app ZIP, original task ZIP and downloaded result ZIP. Use RESUME_RESULT
in a fresh notebook to recover a task. Download portable checkpoints from the PC
app to preserve accepted plan steps. Free runtime availability varies. Use the
notebook validation section to capture the actual environment and result;
before/after GPU memory samples do not measure peak RAM/VRAM.

## Theme or welcome guide resets

Theme choices use browser storage and a preference-only cookie; separate
browsers/hostnames can have different choices. Welcome dismissal lives in the
app data directory. A new data directory is a separate workspace. Reopen the
guide through **Getting started** whenever needed.

## After interruption

The app marks unfinished sessions interrupted on restart. Inspect saved evidence
and retry from an accepted checkpoint. Nothing automatically merges or pushes
changes. Directory copies do not provide an OS sandbox: enable project commands
only after reviewing them.
