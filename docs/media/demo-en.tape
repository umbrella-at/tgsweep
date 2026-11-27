# The English recording at the top of README.md: tgsweep on a made-up account (scripts/demo.py).
# Run from the repository root: vhs docs/media/demo-en.tape
# The text output and the frames in docs/media/check/ are for checking by eye, they are not committed.

Require uv

Output docs/media/demo-en.gif
Output docs/media/check/demo-en.txt

Set Shell "bash"
Set FontFamily "JetBrains Mono"
Set FontSize 19
Set LineHeight 1.2
Set Width 1200
Set Height 760
Set Padding 24
Set Margin 24
Set MarginFill "#F3F5F8"
Set WindowBar Colorful
Set BorderRadius 10
Set TypingSpeed 90ms
Set CursorBlink false
Set Theme { "name": "tgsweep", "background": "#0E1621", "foreground": "#D6E2ED", "selection": "#2B5278", "cursor": "#D6E2ED", "black": "#17212B", "red": "#E8806F", "green": "#8FC48A", "yellow": "#FFB627", "blue": "#6AB3F3", "magenta": "#C79BD8", "cyan": "#7CC4BC", "white": "#D6E2ED", "brightBlack": "#8296A9", "brightRed": "#F29A8B", "brightGreen": "#A9D6A4", "brightYellow": "#FFC85A", "brightBlue": "#9CCBF7", "brightMagenta": "#D8B4E6", "brightCyan": "#9AD6CF", "brightWhite": "#FFFFFF" }

Hide
Type "export PS1='~ % '" Enter
Type "alias tgsweep='uv run --frozen --quiet scripts/demo.py en'" Enter
Type "clear" Enter
Show

Sleep 800ms
Type "tgsweep"
Sleep 500ms
Enter

Wait+Screen@20s /Older than 30 days/
Sleep 1.2s
Down
Sleep 700ms
Down
Sleep 700ms
Down
Sleep 1s
Enter

Wait+Screen@30s /Select the chats/
Sleep 1.5s
Screenshot docs/media/check/demo-en-1-search.png
Space
Sleep 700ms
Down
Sleep 700ms
Space
Sleep 1.2s
Screenshot docs/media/check/demo-en-2-chats.png
Enter

Wait+Screen@10s /type the number 53/
Sleep 1.2s
Type "53"
Sleep 800ms
Screenshot docs/media/check/demo-en-3-confirm.png
Enter

Wait+Screen@30s /Sign out on this computer/
Sleep 1.5s
Enter

Wait+Screen@10s /You are signed out/
Sleep 1s
Screenshot docs/media/check/demo-en-4-final.png
Sleep 2s
