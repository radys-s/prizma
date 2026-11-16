# Launcher (`aplikace.exe`)

`aplikace.exe` in the repository root is built from `Launcher.cs` in this folder.
It is the only binary in the repository.

## Why the binary is committed

- It can only be built on Windows. `build.ps1` calls `csc.exe`, the .NET Framework 4
  compiler that ships with Windows. The hubs and the CI runner are Linux.
- The launcher finds the project through its own location, so it has to sit in the
  repository root. Committing it keeps "clone, then double-click" working.

## Rebuild after changing `Launcher.cs`

On Windows, from the repository root:

```
powershell -ExecutionPolicy Bypass -File tools\launcher\build.ps1
```

Commit the new `aplikace.exe` in the same commit as the source change. Nothing checks
that the two match.

## Known state

The committed `aplikace.exe` was built on 2026-09-02, before the app was renamed. It
still shows "Prizma" in its window title and messages; `Launcher.cs` says "Refrakt".
It needs one rebuild on Windows.

## Moving it out of the repository

That needs a Windows build in CI: a `windows-latest` job that runs `build.ps1` and
attaches `aplikace.exe` to the GitHub release. Once that exists, delete the file here,
add it to `.gitignore`, and change the main README to point at the release download.
