# Bernstein README top-section demo (closes #1112)
#
# Generates docs/demo/demo.gif - the 60-second clip that anchors the
# README's first screen.
#
# Regenerate with:
#   vhs docs/demo/demo.tape
#
# Requires: vhs (brew install vhs).
#
# Flow shown: manager decomposes the goal -> three agents run in parallel
# -> audit chain records each handoff -> janitor verifies -> PR opens.

Output docs/demo/demo.gif

# ---- Display settings ------------------------------------------------------
Set Shell         "bash"
Set FontSize      14
Set FontFamily    "JetBrains Mono"
Set Width         960
Set Height        540
Set Theme         "Dracula"
Set Framerate     24
Set PlaybackSpeed 0.85
Set TypingSpeed   45ms
Set WindowBar     "Rings"
Set Padding       18
Set BorderRadius  8

# ---- Hidden setup: alias bernstein and clean prompt ------------------------
Hide
Type `export PS1="$ " && alias bernstein="bash docs/demo/demo-runner.sh"`
Enter
Sleep 250ms
Show

Sleep 400ms

# User types the goal command
Type `bernstein run -g "Add auth, tests, and docs"`
Sleep 600ms
Enter

# Let the full demo play out (manager -> 3 agents -> audit -> janitor -> PR)
Sleep 18s

# Pause at PR summary so viewers can read it
Sleep 2s
