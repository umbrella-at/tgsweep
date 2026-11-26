# Инфраструктура

## Программа

tgsweep работает на компьютере пользователя, сервера у него нет. Ставят его через uv из архива ветки main:

```sh
uv tool install https://github.com/umbrella-at/tgsweep/archive/refs/heads/main.zip
```

Поэтому каждый push в main сразу получают новые установки и `uv tool upgrade tgsweep`. Git у пользователя не нужен. tgctl ставит tgsweep по тегу, push в main его не задевает.

## Сайт tgsweep.atworks.dev

- Исходники: папка `site/`, статические HTML, CSS, JS и шрифты. Сборки нет, файлы уходят как есть.
- Хостинг: GitHub Pages репозитория umbrella-at/tgsweep, источник GitHub Actions.
- Деплой: workflow `.github/workflows/pages.yml`. Запускается на push в main, если изменилась папка `site/` или сам workflow, и вручную: Actions, pages, Run workflow.
- Домен: в Cloudflare, зона atworks.dev, запись CNAME `tgsweep` на `umbrella-at.github.io` с выключенным прокси (серое облако, DNS only), иначе GitHub не выпустит сертификат. В настройках Pages указан домен tgsweep.atworks.dev и включён Enforce HTTPS. Файл CNAME в `site/` не нужен: при деплое через Actions GitHub его не читает.
- Проверка перед push: `python3 -m http.server -d site 8000` и открыть http://127.0.0.1:8000. Через file:// страница откроется без шрифтов: их не пропускает CSP.
- CSP задана тегом meta в `site/index.html`: скрипты только свои и с gc.zgo.at, отправка данных только на *.goatcounter.com.
- Статистика: GoatCounter, без cookies. В его настройках (Settings, Data collection) включены только Referrer и Sessions, чтобы подпись в подвале сайта была правдой. Код счётчика вставляется в `site/index.html` перед `</body>`.
- Шрифты Golos Text и JetBrains Mono (OFL 1.1) лежат в `site/fonts/` вместе с лицензиями. Они урезаны до латиницы, кириллицы и символов терминала, поэтому весят 44 и 20 КБ.
- Экраны терминала на странице сняты с настоящей программы на выдуманном аккаунте из `tests/fake.py`. Если меняются тексты программы, экраны надо переснять.
- `tests/test_static.py` проверяет, что сайт и README дают одни и те же команды установки и что в текстах сайта нет длинных и коротких тире.
