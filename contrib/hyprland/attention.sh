#!/bin/bash
# Focus the first waiting Claude agent, opening the workspace if needed.
# The waiting colour is set by set-state.sh; both read the same variable so
# the pair cannot drift. hyprctl reports colours as AARRGGBB, hooks set them as
# RRGGBBAA, hence the reorder rather than a second hand-typed constant.
WAITING_RGBA="${CLAUDE_WAITING_COLOR:-fab387cc}"
waiting_argb="${WAITING_RGBA:6:2}${WAITING_RGBA:0:6}"

waiting=""
for addr in $(hyprctl clients -j | jq -r '.[] | select(.workspace.name == "special:claude") | .address'); do
    color=$(hyprctl getprop "address:$addr" inactive_border_color 2>/dev/null)
    if [ "$color" = "$waiting_argb 0deg" ]; then
        waiting="$addr"
        break
    fi
done

if [ -z "$waiting" ]; then
    notify-send -u low "Claude Workspace" "No agents waiting"
    exit 0
fi

if ! hyprctl monitors -j | grep -q '"special:claude"'; then
    hyprctl dispatch 'hl.dsp.workspace.toggle_special("claude")' >/dev/null 2>&1
fi
hyprctl dispatch "hl.dsp.focus({ window = 'address:${waiting}' })" >/dev/null 2>&1
