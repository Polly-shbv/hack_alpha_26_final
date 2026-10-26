# QA-report — Альфа Контекст 1.0.0

Архивный отчёт версии 1.0 от 24.10.2026, Москва. Для текущей версии 1.1 см. `middle-steps.md` и `audit/middle-steps-check.json`: корпус из кейса получен, OCR и RSS добавлены. Приведённые ниже количества тестов и ограничения описывают именно прежний выпуск.

## Проверенная версия и среда

macOS 27.0.1, arm64; Python 3.12, Node 24.22.1, Playwright 1.62.1 с установленным Chromium. Версия исходников и SHA256: audit/version.json. Git-репозитория в исходной папке не было, удалённая публикация не выполнялась.

Из-за зависающих read() у части файлов в macOS Documents итоговый release command выполнен на полной локальной копии текущих исходников `/private/tmp/alfa-context-clean-check`, с чистой установкой по тем же lock-файлам. Изменённые backend/frontend/tests/configs скопированы точно; это не подменённый HTML. Preview использует ту же проверенную копию; БД и пользовательские PDF сохраняются в исходном проекте. Локальный .runtime-dir позволяет scripts/dev повторно использовать эту копию. Production build автоматически изолируется scripts/build. Особенность среды не выдана за ошибку приложения или успешное выполнение зависшего первоначального процесса.

## Финальные команды и фактические результаты

| Команда / проверка | Результат |
|---|---|
| `./scripts/bootstrap` в новой папке без .venv/node_modules/БД | PASS: 25 зафиксированных Python packages, pnpm frozen lock, миграция и seed с нуля |
| `./scripts/check` — полный финальный прогон | PASS, exit 0 |
| Python syntax/compileall | PASS |
| `python -m pytest backend/tests -q` | **53 passed**, 3,15 s в финальном release; 1 upstream DeprecationWarning Starlette/AnyIO |
| `node scripts/frontend-unit.mjs` | **9 passed**, 0 failed; около 94 ms |
| `./scripts/test-e2e` | **14 passed**, 46,3 s; живые FastAPI + React, не mock HTML |
| TypeScript strict `tsc --noEmit` | PASS в чистой/изолированной сборке |
| Vite production build | PASS, 1,28 s; JS 343,16 kB / gzip 104,76 kB, CSS 25,86 kB / gzip 5,94 kB |
| Принятая screenshot baseline S01, desktop/mobile | PASS; threshold 0,5%, геометрия ±2px |
| axe serious/critical на 10 основных представлениях, desktop/mobile | **0**; audit/axe-results.json |
| Матрица screenshot/overflow 1440/1280/768/390, smoke 320 | PASS в финальном E2E; 40 screenshot cases + 10 smoke cases |
| Дополнительная независимая матрица, включая 1600 | PASS, 60 сочетаний; audit/visual-results.json |
| Свежая БД и startup health | PASS, **490,56 ms**, отдельный локальный порт; audit/startup.json |
| API list p95 на 3 профилях / 30 событиях / 9 PDF | PASS, **4,82 ms**, 40 samples, TestClient без LLM; audit/performance.json |
| Реальная LLM / live regression | **NOT_RUN**: нет утверждённого API, точных ID, тарифов и ключа; владелица поручила fixture |
| Docker Compose runtime | **NOT_RUN**: Docker отсутствует; конфигурация подготовлена, не объявлена проверенной |
| OCR сканов | **NOT_AVAILABLE**: локального tesseract нет; автоматическая диагностика needs_ocr проверена |

Vitest ранее выполнил 9 проверок, но его worker в исходной папке зависал при чтении зависимостей. В release используется независимый Node test runner для тех же frontend contracts; этот обход и исходные зависшие прогоны не посчитаны успешным Vitest release. Первые visual-прогоны с timeout/ошибками axe и неверным шаблоном PNG были исправлены и повторены; в таблице приведён финальный успешный результат.

## Что проверено по смыслу

- Три выбранных ID сохранены. Полные CSV просканированы; подтверждены точные revenue Decimal, ОКВЭД/регионы и 19/218, 504/110, 633/190 контрагентов. Все агрегаты первого этапа пересчитаны и совпали: 85 514 / 115 / 102 / 62 / 49 / 57 / 105. 11 конфликтующих duplicate company rows отклонены и перечислены в dataset-report.json; исходники не менялись.
- CSV encodings/delimiters, string IDs, unknown отдельно от false, repeated headers, конфликтующие дубликаты, negative payments, self-payments, orphan checks; ZIP traversal/тип/размер, PDF magic/page/size, scan needs_ocr.
- Ranking exact components, отсутствие сетевого бонуса от числа связей, geography=5 только по стране ФТС, penalties, monotonicity missing fields, future cutoff.
- **18 golden cases**: 6 событий × 3 компании; это 18 содержательных итераций внутри теста, не 18 дополнительных pytest-тестов. Нет фиктивных URL, банковских утверждений, чужих field IDs, будущих материалов. Отдельный позитивный case требует исходную предпосылку и условия, выдуманное число отвергается.
- Facts требуют точной поддерживающей выдержки; неподходящий fragment, несуществующие/чужие IDs и unverified bank claim отклоняются. Конфликт сроков остаётся missing_data. Это не измеренная точность на реальных клиентах.
- Полный семишаговый orchestrator: snapshot → поиск → FTS → рейтинг → interpretation → validation/adaptive search → атомарная публикация. Проверены повторные Idempotency-Key, отмена во время интерпретации без публикации partial, перезапуск и сохранения, отдельные версии роли/корпуса. Исторический run сохраняет профиль даже после нового импорта.
- Budget: параллельные резервы, потолок, смена московского дня, uncertain charging, неизвестный тариф, запрещённый ID, mocked usage, bounded retries 429/500. Все adapter tests — HTTP mocks, платных вызовов нет.
- RBAC viewer/admin, CSRF, foreign company/insight/export, приватность jobs/files, SSRF private/loopback/IPv6/credentials/mixed DNS, sanitization/prompt-injection fixture. Реальные external URL feeds и внешний SSO не объявлены проверенными интеграциями.
- E2E: выбор/роль/reload, реальный анализ, смена компании, шесть блоков/source drawer, поиск/topics/Back, save/remove, сравнение, PDF import/job status, missing key, Markdown, feedback, old/current history, карта/text list, deep link, dirty form guard.

**AI-помощник: интерфейсная заглушка, модели не подключены.** Desktop/mobile: точный текст «Скоро», нет input/textarea/send, закрытие кнопкой/крестиком/Escape/overlay и возврат фокуса. Fetch/XHR/WebSocket при открытии/закрытии: 0; ledger не изменился. Chat endpoints отсутствуют.

## Визуальная проверка

Скриншоты: `docs/screenshots/`. Самостоятельно осмотрены S01, бриф desktop/mobile, лента, статья, мобильный импорт и карта. Исправлены обрезанный край карты, нижняя навигация, склонение «риска/рисков», контраст сиреневого профиля и различимость ссылки. Baseline S01 принят после осмотра; остальные экраны проверены responsive/axe/overflow, полного pixel-perfect совпадения со всеми растровыми картинками PDF не заявлено.

Основные артефакты: S01-1440.png, S02-1440.png, S02-390.png, S03-1440.png, S04-1440.png, S07-390.png, map-1440.png, assistant-1440.png, assistant-390.png. У финального Playwright прогона 0 проваленных tests; HTML report сохранён в playwright-report/index.html. Traces сохраняются при ошибках; старые failed traces не являются evidence финального pass.

## Оставшиеся ограничения

- Нет подлинных 9 PDF банка/согласованных RSS и API-инструкции. Fixture bank_view честно not_found. Учебные файлы не превращаются в реальные сменой подписи; замены перечислены в test-data-replacement.md.
- Live adapter проверен на OpenAI-compatible mocks; совместимость с ещё неизвестным протоколом организаторов и качество реальной модели не проверены. Условные выводы не являются позицией банка или прогнозом с доказанной точностью.
- Один локальный worker, а не распределённая очередь. При crash незавершённый job становится failed и требует явного повторного запуска, старый snapshot сохраняется. Remote auth/SSO, provider-wide shared-key quota и production security review вне локального результата.
- PDF просматривается через извлечённые страницы/текст и безопасное скачивание; полноценный встроенный PDF viewer и OCR не заявлены.
- Временная демонстрационная эмблема, не официальный бренд SVG. Субъективное согласование бренда не заменено тестами.

Для текущего тестового режима действий от владелицы не требуется. Приложение не опубликовано, банковские операции и рассылки не выполнялись.
