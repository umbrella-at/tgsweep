# The Russian recording at the top of README.ru.md: tgsweep on a made-up account (scripts/demo.py).
# Run from the repository root: vhs docs/media/demo-ru.tape
# The text output and the frames in docs/media/check/ are for checking by eye, they are not committed.

Require uv

Output docs/media/demo-ru.gif
Output docs/media/check/demo-ru.txt

Set Shell "bash"
Set FontFamily "JetBrains Mono"
Set FontSize 19
Set LineHeight 1.2
Set Width 1200
Set Height 760
Set Padding 24
Set Margin 24
Set MarginFill "#E3EAE4"
Set WindowBar Colorful
Set BorderRadius 10
Set TypingSpeed 90ms
Set CursorBlink false
Set Theme { "name": "tgsweep", "background": "#132620", "foreground": "#D3E0D7", "selection": "#2B4A3E", "cursor": "#D3E0D7", "black": "#1B3129", "red": "#E8806F", "green": "#8FC48A", "yellow": "#FFB627", "blue": "#79A3CC", "magenta": "#C79BD8", "cyan": "#7CC4BC", "white": "#D3E0D7", "brightBlack": "#8BA597", "brightRed": "#F29A8B", "brightGreen": "#A9D6A4", "brightYellow": "#FFC85A", "brightBlue": "#9BBEE0", "brightMagenta": "#D8B4E6", "brightCyan": "#9AD6CF", "brightWhite": "#F1F7F3" }

Hide
Type "export PS1='~ % '" Enter
Type "alias tgsweep='uv run --frozen --quiet scripts/demo.py ru'" Enter
Type "clear" Enter
Show

Sleep 800ms
Type "tgsweep"
Sleep 500ms
Enter

Wait+Screen@20s /Старше 30 дней/
Sleep 1.2s
Down
Sleep 700ms
Down
Sleep 700ms
Down
Sleep 1s
Enter

Wait+Screen@30s /Отметьте чаты/
Sleep 1.5s
Screenshot docs/media/check/demo-ru-1-search.png
Space
Sleep 700ms
Down
Sleep 700ms
Space
Sleep 1.2s
Screenshot docs/media/check/demo-ru-2-chats.png
Enter

Wait+Screen@10s /введите число 53/
Sleep 1.2s
Type "53"
Sleep 800ms
Screenshot docs/media/check/demo-ru-3-confirm.png
Enter

Wait+Screen@30s /Выйти из аккаунта на этом компьютере/
Sleep 1.5s
Enter

Wait+Screen@10s /Вы вышли из аккаунта/
Sleep 1s
Screenshot docs/media/check/demo-ru-4-final.png
Sleep 2s
