-- Claude workspace: scrolling tape for Claude Code agents.
--
-- A window is treated as a Claude agent when its app id is claude-code (what
-- claude spawn sets), or when it is a foot window whose title starts with
-- "Claude Code", the Claude prefix mark, or a braille spinner frame. Such
-- windows are routed to special:claude with a green border; intruders that
-- land there are evicted to workspace 1. Closing the last agent auto-hides
-- the workspace.

local mainMod = "SUPER"

-- Where you copied contrib/hyprland/. Set CLAUDE_HYPR_DIR if it is somewhere else.
local SCRIPTS = os.getenv("CLAUDE_HYPR_DIR") or (os.getenv("HOME") .. "/.config/hypr/scripts")

local CLAUDE_WS     = "special:claude"
local CLAUDE_BORDER = "rgba(a6e3a1cc)"

-- One pattern per glyph, never a character class: Lua patterns match bytes, so
-- "[⠁⠂⠄]" is a set of UTF-8 fragments and a trailing space can never follow one.
local SPINNER_PATTERNS = { "^Claude Code", "^✳ " }
for _, glyph in ipairs({ "⠁", "⠂", "⠄", "⡀", "⠈", "⠐", "⠠", "⢀" }) do
    SPINNER_PATTERNS[#SPINNER_PATTERNS + 1] = "^" .. glyph .. " "
end

------------------------------------------------------------------------------
-- Layout / look
------------------------------------------------------------------------------

hl.workspace_rule({
    workspace = CLAUDE_WS,
    layout    = "scrolling",
})

hl.config({
    scrolling = {
        column_width             = 0.667,
        fullscreen_on_one_column = false,
        follow_focus             = true,
        focus_fit_method         = 0,
        explicit_column_widths   = "0.5, 0.667, 1.0",
    },
    decoration = { dim_special = 0.4 },
})

------------------------------------------------------------------------------
-- Watcher
------------------------------------------------------------------------------

local function is_claude_terminal(w)
    if not w then return false end
    -- A session typed into a plain foot window is only recognisable by its title.
    if w.class == "claude-code" then return true end
    if w.class ~= "foot" then return false end
    local t = w.title or ""
    for _, p in ipairs(SPINNER_PATTERNS) do
        if t:match(p) then return true end
    end
    return false
end

local moved = {}  -- address -> true

local function pull_into_claude(w)
    if moved[w.address] then return end
    moved[w.address] = true

    local sel = "address:" .. w.address
    hl.dispatch(hl.dsp.window.move({ workspace = CLAUDE_WS, follow = false, window = sel }))
    hl.dispatch(hl.dsp.window.set_prop({ prop = "active_border_color",
                                         value = CLAUDE_BORDER, window = sel }))
    hl.dispatch(hl.dsp.window.set_prop({ prop = "inactive_border_color",
                                         value = CLAUDE_BORDER, window = sel }))
end

local function evict_from_claude(w)
    hl.dispatch(hl.dsp.window.move({ workspace = "1", follow = false,
                                     window = "address:" .. w.address }))
end

hl.on("window.open", function(w)
    if is_claude_terminal(w) then
        pull_into_claude(w)
    elseif w.workspace and w.workspace.name == CLAUDE_WS then
        evict_from_claude(w)
    end
end)

hl.on("window.title", function(w)
    if is_claude_terminal(w) then
        pull_into_claude(w)
    end
end)

hl.on("window.close", function(w)
    moved[w.address] = nil
    -- Auto-hide special:claude when its last window leaves.
    hl.timer(function()
        local ws  = hl.get_workspace(CLAUDE_WS)
        local mon = hl.get_active_monitor()
        if ws and ws.is_empty
           and mon and mon.active_special_workspace
           and mon.active_special_workspace.name == CLAUDE_WS then
            hl.dispatch(hl.dsp.workspace.toggle_special("claude"))
        end
    end, { timeout = 100, type = "oneshot" })
end)

-- Sweep pre-existing windows (covers Hyprland start and config reload).
for _, w in ipairs(hl.get_windows()) do
    if is_claude_terminal(w)
       and (not w.workspace or w.workspace.name ~= CLAUDE_WS) then
        pull_into_claude(w)
    end
end

------------------------------------------------------------------------------
-- Autostart
------------------------------------------------------------------------------

hl.on("hyprland.start", function()
    -- Sweep windows that were already open when this config loaded.
    hl.timer(function()
        for _, w in ipairs(hl.get_windows()) do
            if is_claude_terminal(w)
               and (not w.workspace or w.workspace.name ~= CLAUDE_WS) then
                pull_into_claude(w)
            end
        end
    end, { timeout = 5000, type = "oneshot" })
end)

------------------------------------------------------------------------------
-- Bindings
------------------------------------------------------------------------------

hl.bind(mainMod .. " + C",        hl.dsp.workspace.toggle_special("claude"),
        { description = "Peek into the Claude Workspace" })
hl.bind(mainMod .. " + SHIFT + C", hl.dsp.exec_cmd("claude spawn"),
        { description = "Summon a fresh Claude agent" })
hl.bind(mainMod .. " + ALT + C",   hl.dsp.exec_cmd(SCRIPTS .. "/attention.sh"),
        { description = "Focus the next waiting agent" })
hl.bind(mainMod .. " + CTRL + C",  hl.dsp.exec_cmd(SCRIPTS .. "/chat-menu.py"),
        { description = "Pick or start a chat" })
