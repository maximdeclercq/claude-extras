#!/bin/bash
# Set border color on this Claude Code window and notify on waiting. Called by hooks.
# CLAUDE_WAITING_COLOR is what attention.sh matches on, so it is read from
# the same variable here rather than typed a second time in another byte order.
case "$1" in
    running) color="rgba(89b4facc)" ;;
    waiting) color="rgba(${CLAUDE_WAITING_COLOR:-fab387cc})" ;;
    done)    color="rgba(a6e3a1cc)" ;;
    *)       exit 0 ;;
esac

TERMINAL="${CLAUDE_TERMINAL:-foot}"
ATTENTION="${CLAUDE_ATTENTION_SCRIPT:-$(dirname "$(readlink -f "$0")")/attention.sh}"

# Walk /proc to the terminal's pid. ppid is the second field after the comm, and
# comm is parenthesised and may itself contain spaces and parens, so the split
# has to happen at the LAST ')' rather than on whitespace from the start.
pid=$$
term_pid=""
while [ "$pid" -gt 1 ]; do
    if [ "$(cat "/proc/$pid/comm" 2>/dev/null)" = "$TERMINAL" ]; then
        term_pid=$pid
        break
    fi
    stat=$(cat "/proc/$pid/stat" 2>/dev/null) || break
    pid=$(printf '%s' "${stat##*) }" | cut -d' ' -f2)
    [ -n "$pid" ] || break
done
[ -z "$term_pid" ] && exit 0

# Single hyprctl call: find address and verify workspace
addr=$(hyprctl clients -j | jq -r --argjson pid "$term_pid" --arg class "$TERMINAL" \
    '.[] | select(.pid == $pid and .class == $class and .workspace.name == "special:claude") | .address // empty')
[ -z "$addr" ] && exit 0

hyprctl dispatch "hl.dsp.window.set_prop({ prop = 'active_border_color',   value = '$color', window = 'address:$addr' })" >/dev/null 2>&1
hyprctl dispatch "hl.dsp.window.set_prop({ prop = 'inactive_border_color', value = '$color', window = 'address:$addr' })" >/dev/null 2>&1

if [ "$1" = "waiting" ]; then
    (
        action=$(notify-send -u normal -a "Claude Code" "Agent needs attention" \
            -i dialog-question -t 10000 \
            --action="focus=Focus" 2>/dev/null)
        if [ "$action" = "focus" ] && [ -x "$ATTENTION" ]; then
            "$ATTENTION"
        fi
    ) &
fi
