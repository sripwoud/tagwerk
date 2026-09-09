# Build a small tracker instead of adopting ActivityWatch or wakapi

The requirement is passive per-repo attribution on Hyprland with zero manual start/stop and corrections from the CLI. ActivityWatch with awatcher sees window titles but not the terminal cwd, and Claude Code overwrites the kitty title; wakapi's heartbeats are editor-centric and its corrections live in a web UI; every CLI tracker is manual, which is what killed the timewarrior setup. We build roughly 350 lines of stdlib Python on top of what the desktop already exposes: Hyprland focus and pid, kitty remote control for cwd, hypridle for idle, Claude Code and pi hooks for agent beats.

Full survey with sources: `docs/research/2026-09-09-solution-space.md`.
