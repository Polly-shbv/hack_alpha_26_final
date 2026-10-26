# Контракты данных

Канонический API: `/api/v1`; живой OpenAPI `/openapi.json`. Листинги имеют items/total/page/page_size, ошибки error.code/message/details/request_id. Клиент не выводит SQL, stacktrace или raw provider errors.

Company: string ID, точная decimal revenue в строке, nullable product flags, страны ФТС, ОКВЭД/регион из исходного CSV. field_evidence содержит raw field/value, source file, row key и imported_at. Дополнительные уточнения версионируются отдельно; конфликт версий 409. Город физлица не является регионом компании.

Source: immutable extracted text/chunks, SHA256, published/fetched timestamps, origin URL либо null, is_synthetic, bank_verified, mode, active. Каждый chunk имеет offset и page_number для PDF. Повторный SHA256 не создаёт копии. Отозванный источник исключается из новых runs; snapshot истории сохраняет прежние evidence refs.

InsightVersion: company_id/event_id/run_id/role/mode/as_of + facts/company_links/bank_view/hypotheses/actions/missing_data/assumptions + relevance/evidence_status. Facts в MVP extractive; ссылка на несуществующий или неподходящий fragment отвергается. Hypotheses содержат premise_claim_ids, company field IDs и явные условия. bank_view claims требуют точную цитату из реального verified PDF, не учебного файла. Полнота evidence не является вероятностью прогноза.

Budget: Decimal, московская дата, reserved/charged/state. Состояния reserved/settled/uncertain. Повторные settle не списывают дважды. До тарифа live blocked.

CSV: UTF-8 BOM/UTF-8/CP1251, автоматический delimiter; денежные значения Decimal; unknown и false различаются. Проверки дубликатов, повторного заголовка, отрицательных платежей и orphan links с отчётом. Сырые файлы остаются неизменными. Для локального MVP сохраняются выбранные профили и их aggregate links, не все персональные связи. ZIP: только CSV, до 100 entries/200 МБ; path traversal/symlink/type/bomb checks.

Исторический as_of ограничивает корпус. UI date range включителен по Москве. Fixture default: 23.10.2026. Даты не объявляются «сегодня» при несовпадении даты пользователя.
