# Changelog

Tutte le modifiche rilevanti di questo progetto sono documentate in questo file.

Il formato segue [Keep a Changelog](https://keepachangelog.com/it/1.1.0/) e il progetto aderisce al
[Semantic Versioning](https://semver.org/lang/it/).

## [Unreleased]

Ciclo di sviluppo **v2.0**: passaggio a MetaTrader 5 con esecuzione ordini diretta da Python
(package ufficiale `MetaTrader5`), eliminazione dell'Expert Advisor e del ponte CSV.

## [1.0.0] - 2026-07-13

Versione stabile per **MetaTrader 4**: crawler Telegram (Python/Telethon) + Expert Advisor MQL4
comunicanti via CSV. Congelata nel tag `v1.0.0`; eventuali hotfix sul branch `release/v1.x`.

### Added
- Crawler Telegram: riconoscimento di 10 tipi di messaggio (piazzamento pendente e a mercato,
  apertura, modifica, spostamento stop loss — anche mirato via reply Telegram —, chiusura singola
  e multipla, annullamento, notifiche di chiusura automatica).
- Expert Advisor MQL4: esecuzione ordini da CSV con magic number, slippage in pip configurabile,
  lotto validato sui limiti del simbolo, registro ordini e stato persistente.
- Catch-up dei messaggi persi durante i downtime del crawler.
- Esecuzione come servizio Windows (Scheduled Task con riavvio automatico).
- Validazione del config all'avvio; 50 unit test; lint ruff; CI GitHub Actions.

### Fixed
- Registro ordini troncato a ogni scrittura; registro non aggiornato dopo le modify.
- Riesecuzione dei segnali storici al riavvio dell'EA; segnali nello stesso secondo persi.
- Posizione duplicata sul messaggio "ordine aperto" con pending già triggerato.
- Multi-close che chiudeva una sola posizione (e sull'asset sbagliato nei messaggi multi-asset).
- Ordini a mercato inviati con prezzo 0 (rifiutati dal broker).
- Matching nel registro con tolleranza fissa inadatta alle coppie JPY.

[Unreleased]: https://github.com/sampiovan/trading-signals-crawling/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/sampiovan/trading-signals-crawling/releases/tag/v1.0.0
