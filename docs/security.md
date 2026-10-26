# Безопасность локального прототипа

- Разработка bind 127.0.0.1. Demo session HttpOnly/SameSite=Strict, CSRF header на mutation и проверка Origin. Allowlist компаний и системная роль — в серверной session; представление ceo/cfo/treasury не меняет прав. Это не банковский SSO.
- Docker доступен через 127.0.0.1:8000; ALFA_CONTAINER_LOCAL допускает gateway внутри контейнера только при такой конфигурации. Не публиковать этот образ с demo auth в интернет.
- Для внешнего live развёртывания потребуется отдельно созданная доверенной системой server session; demo guard отключается ALFA_AUTH_MODE. Полноценный поток sign-in/SSO не входит в локальный результат.
- SSRF: http(s), без credentials и произвольных портов; IPv6 запрещён в загрузчике MVP; все DNS-адреса проверяются на global; socket соединяется с проверенным IP, TLS SNI сохраняется. Каждый redirect проверяется заново; до 3 redirects, 15s, 10 МБ. Модель не имеет произвольного HTTP/shell/tools/settings.
- PDF magic bytes, 20 МБ, 300 страниц, encrypted PDF rejected, безопасные имена, скачивание attachment/nosniff/sandbox. Извлечение не исполняет вложения. Скан получает needs_ocr, если OCR недоступен.
- UI использует text nodes; HTML очищается при импорте. CSP, безопасные внешние ссылки. Тексты источников — недоверенные данные, не команды модели.
- API key только .env/server environment. Нет ввода в браузере, localStorage или client bundle. Settings возвращает только key_set. Trace содержит инструментальные results без ключей и персональных связей.
- Все company/profile/insight/export/saved/feedback/run endpoints проверяют доступ. Sources/files/jobs проверяют owner; run search ограничен собственными/публичными источниками. Все действия локальны, платежи/кредитные заявки отсутствуют.
- SQLite и uploads не зашифрованы на уровне приложения. Для локального прототипа использовать FileVault и ограничение доступа к папке. Реальный production security review и SSO не объявлены выполненными.
