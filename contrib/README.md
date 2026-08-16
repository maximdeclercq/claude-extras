# contrib

Desktop integration, none of it required. The wrapper knows nothing about your
compositor or your bar; these are the pieces that made it feel native on one
Hyprland setup. Copy what you want.

One directory per desktop, so it is obvious what applies to you and where a
setup for another compositor would go.

| file | what it does |
|---|---|
| `hyprland/workspace.lua` | Routes every Claude terminal into a `special:claude` scrolling workspace, green-borders it, and hides the workspace when the last one closes |
| `hyprland/set-state.sh` | Called from hooks; colours a window's border by state and notifies when a session wants input |
| `hyprland/attention.sh` | Focuses the next session that is waiting |
| `waybar/chat-button.py` | What the bar button shows, from `claude chat account` |
| `waybar/chat-menu.py` | A launcher menu over `claude chat --json`, to pick a chat or start one |
| `waybar/chat-module.jsonc` | The waybar module wiring those two to a button |

## Hyprland

`hyprland/workspace.lua` is a sub-module of a Lua Hyprland config. It matches
windows by the `claude-code` app id that `claude spawn` sets, and a plain `foot`
window by its title, so a session started by typing `claude` is routed too.

Bindings it adds, all on the `C` cluster:

    SUPER + C          peek into the Claude workspace
    SUPER + SHIFT + C  claude spawn        (a session in the focused directory)
    SUPER + ALT + C    focus the next waiting session
    SUPER + CTRL + C   chat-menu.py        (pick or start a chat)

## Hooks

`hyprland/set-state.sh` expects to be called from `settings.json`:

    "hooks": {
      "UserPromptSubmit":  [{ "hooks": [{ "type": "command", "command": ".../hyprland/set-state.sh running" }] }],
      "PermissionRequest": [{ "hooks": [{ "type": "command", "command": ".../hyprland/set-state.sh waiting" }] }],
      "Stop":              [{ "hooks": [{ "type": "command", "command": ".../hyprland/set-state.sh done" }] }]
    }

`settings.json` is per account and never resynced, so add these to each account
you want coloured. The `SessionEnd` hook that `chat` depends on is not
desktop-specific, and `claude auth doctor --fix` installs it on every account
itself.

## Waybar

See the comment in `waybar/chat-module.jsonc`. `chat-menu.py` tries wofi, fuzzel,
rofi and dmenu in that order, and `CLAUDE_CHAT_ACCOUNTS`, a space-separated list
of names, puts those accounts first in its list. `#d97757` is Claude's own orange,
read out of claude.ai's stylesheets:

    #custom-chat { color: #d97757; }
    #custom-chat.unconfigured { color: @overlay0; }

The module also emits a `class` naming the account (dots become dashes, since a
CSS selector cannot contain them), so colouring per account instead is a matter of
keying on it the way `custom/record` styles `.recording`:

    #custom-chat.acme-dev { color: @blue; }
    #custom-chat.globex-io { color: @green; }
    #custom-chat.personal   { color: @mauve; }

Pick one or the other. With a glyph as the label, the
colour is the only at-a-glance signal of where a click will land; the tooltip
always spells it out either way.
